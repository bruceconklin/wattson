"""Wattson: Discord bot for IPMI power control and Docker container control."""

import asyncio
import os
import re
import signal
import time

import discord
from discord import app_commands

# ---------- Env ----------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

IPMI_HOST = os.environ.get("IPMI_HOST")
IPMI_USER = os.environ.get("IPMI_USER")
IPMI_PASS = os.environ.get("IPMI_PASS")
IPMI_INTERFACE = os.environ.get("IPMI_INTERFACE", "lanplus")  # Supermicro often works best with lanplus
IPMI_TIMEOUT = 30  # seconds; ipmitool retries on its own, this caps the total wait

# Optional (recommended for faster slash command updates while testing)
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")  # e.g. "123456789012345678"

# Optional: restrict who can use Wattson by role
ALLOWED_ROLE_ID = os.environ.get("DISCORD_ALLOWED_ROLE_ID")  # e.g. "234567890123456789"

# Optional: Docker server control over SSH. Leave DOCKER_SSH_HOST empty to
# hide the /docker commands. Server side setup is in server/SETUP.md.
DOCKER_SSH_HOST = os.environ.get("DOCKER_SSH_HOST", "").strip()
DOCKER_SSH_USER = os.environ.get("DOCKER_SSH_USER", "").strip() or "wattson"
SSH_DIR = os.environ.get("WATTSON_SSH_DIR", "/data/ssh")
DOCKER_TIMEOUT = 120  # seconds; stopping a game server can take a while
CONTAINER_CACHE_TTL = 60  # seconds between container list refreshes for autocomplete

CONTAINER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN env var is required.")
if not IPMI_HOST or not IPMI_USER or not IPMI_PASS:
    raise RuntimeError("IPMI_HOST, IPMI_USER, IPMI_PASS env vars are required.")


# ---------- Subprocess helper ----------
async def run_cmd(cmd: list[str], timeout: float, env: dict[str, str] | None = None) -> str:
    """Run a command without blocking the event loop, and kill it if it overruns.

    Blocking calls here used to freeze the whole bot (Discord heartbeat
    stalls) whenever the BMC was slow to answer.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,  # own process group, so a timeout can kill any children too
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as e:  # timeout or cancellation: don't leave the process behind
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
        if isinstance(e, asyncio.TimeoutError):
            raise RuntimeError(f"timed out after {timeout:.0f}s") from None
        raise

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(err or out or f"{cmd[0]} failed with code {proc.returncode}")
    return out


def clip(text: str, limit: int = 1800) -> str:
    """Keep messages under Discord's 2000 character limit."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------- IPMI helper ----------
async def run_ipmi(args: list[str]) -> str:
    # -E reads the password from IPMI_PASSWORD, so it stays off the command line.
    cmd = ["ipmitool", "-I", IPMI_INTERFACE, "-H", IPMI_HOST, "-U", IPMI_USER, "-E", *args]
    env = {**os.environ, "IPMI_PASSWORD": IPMI_PASS}
    return await run_cmd(cmd, IPMI_TIMEOUT, env=env)


# ---------- Docker server helper ----------
async def run_ctl(*args: str, timeout: float = DOCKER_TIMEOUT) -> str:
    """Run a wattson-ctl command on the Docker server over SSH.

    The server only lets Wattson's key run wattson-ctl, which limits it to
    list/start/stop/restart on allowlisted containers, plus reboot.
    """
    cmd = [
        "ssh",
        "-i", os.path.join(SSH_DIR, "id_ed25519"),
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={os.path.join(SSH_DIR, 'known_hosts')}",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "LogLevel=ERROR",
        f"{DOCKER_SSH_USER}@{DOCKER_SSH_HOST}",
        *args,
    ]
    try:
        return await run_cmd(cmd, timeout)
    except RuntimeError as e:
        msg = str(e).removeprefix("error: ")
        if "Permission denied" in msg:
            msg += " (is Wattson's SSH key authorized on the Docker server? See server/SETUP.md)"
        raise RuntimeError(msg) from None


STATE_EMOJI = {
    "running": "🟢",
    "restarting": "🟡",
    "paused": "🟡",
    "created": "🟡",
    "exited": "🔴",
    "dead": "🔴",
    "missing": "⚪",
}

_container_cache: dict = {"at": 0.0, "names": []}
_refresh_task: asyncio.Task | None = None


async def docker_list() -> list[tuple[str, str, str]]:
    """Return (name, state, status) for every allowlisted container."""
    out = await run_ctl("list", timeout=20)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    _container_cache["names"] = [name for name, state, _ in rows if state != "missing"]
    _container_cache["at"] = time.monotonic()
    return rows


async def docker_action(action: str, name: str) -> str:
    state = await run_ctl(action, name)
    _container_cache["at"] = 0.0  # state changed; refresh on next autocomplete
    return f"{STATE_EMOJI.get(state, '❔')} `{name}` is now **{state}**."


async def container_names_for_autocomplete() -> list[str]:
    """Container names for autocomplete, without making Discord wait.

    Discord drops autocomplete answers that take more than about 3 seconds,
    so a slow refresh keeps running in the background and the last known
    list is used meanwhile.
    """
    global _refresh_task
    if time.monotonic() - _container_cache["at"] > CONTAINER_CACHE_TTL:
        if _refresh_task is None or _refresh_task.done():
            _refresh_task = asyncio.create_task(docker_list())
            # Mark any failure as handled so asyncio doesn't log it as unretrieved.
            _refresh_task.add_done_callback(lambda t: t.cancelled() or t.exception())
        try:
            await asyncio.wait_for(asyncio.shield(_refresh_task), timeout=2.0)
        except Exception:
            pass
    return _container_cache["names"]


# ---------- Access control ----------
def user_allowed(interaction: discord.Interaction) -> bool:
    if not ALLOWED_ROLE_ID:
        return True
    try:
        role_id = int(ALLOWED_ROLE_ID)
    except ValueError:
        return False

    member = interaction.user
    if not hasattr(member, "roles"):
        return False
    return any(getattr(r, "id", None) == role_id for r in member.roles)


async def guard(interaction: discord.Interaction) -> bool:
    if user_allowed(interaction):
        return True
    await interaction.response.send_message("You are not allowed to use Wattson commands.", ephemeral=True)
    return False


# ---------- Discord client ----------
class Wattson(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Sync commands either to one guild (fast) or globally (slower propagation)
        if GUILD_ID:
            try:
                guild = discord.Object(id=int(GUILD_ID))
            except ValueError:
                print(f"[Wattson] Invalid DISCORD_GUILD_ID={GUILD_ID!r}. Syncing globally instead.")
                await self.tree.sync()
                return

            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"[Wattson] Slash commands synced to guild {GUILD_ID}")
        else:
            await self.tree.sync()
            print("[Wattson] Slash commands synced globally (may take time to appear)")


client = Wattson()


@client.event
async def on_ready():
    print(f"[Wattson] Logged in as {client.user} (id={client.user.id})")
    if DOCKER_SSH_HOST:
        print(f"[Wattson] Docker control enabled for {DOCKER_SSH_USER}@{DOCKER_SSH_HOST}")
    else:
        print("[Wattson] Docker control disabled (DOCKER_SSH_HOST is empty)")


# ---------- Response helpers ----------
async def respond_with(interaction: discord.Interaction, action) -> None:
    """Defer, run `action` (an async callable returning the reply), and report."""
    await interaction.response.defer(ephemeral=True)
    try:
        text = await action()
    except Exception as e:
        text = f"❌ Error: `{e}`"
    await interaction.followup.send(clip(text), ephemeral=True)


class ConfirmView(discord.ui.View):
    """Confirm/Cancel buttons that only the person who ran the command can press."""

    def __init__(self, *, requester_id: int, command: str, confirm_label: str,
                 working_text: str, action, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.requester_id = requester_id
        self.command = command
        self.working_text = working_text
        self.action = action  # async callable returning the result message
        self.prompt_interaction: discord.Interaction | None = None  # set once the prompt is sent
        self.completed = False
        self.confirm.label = confirm_label

    def _disable(self):
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                f"Only the person who ran `{self.command}` can confirm or cancel.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        if self.completed:
            return
        self.completed = True
        self._disable()
        if self.prompt_interaction is not None:
            try:
                await self.prompt_interaction.edit_original_response(
                    content="⌛ Timed out. No action taken.", view=self
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.completed:
            await interaction.response.send_message("Already handled.", ephemeral=True)
            return
        self.completed = True
        self.stop()
        self._disable()

        # Update the prompt in place so the buttons can't be pressed twice.
        await interaction.response.edit_message(content=self.working_text, view=self)
        try:
            text = await self.action()
        except Exception as e:
            text = f"❌ Error: `{e}`"
        try:
            await interaction.edit_original_response(content=clip(text), view=None)
        except discord.HTTPException:
            await interaction.followup.send(clip(text), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.completed:
            await interaction.response.send_message("Already handled.", ephemeral=True)
            return
        self.completed = True
        self.stop()
        self._disable()

        try:
            await interaction.response.edit_message(content="✅ Cancelled. No action taken.", view=self)
        except (discord.NotFound, discord.HTTPException):
            try:
                await interaction.response.send_message("✅ Cancelled. No action taken.", ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send("✅ Cancelled. No action taken.", ephemeral=True)


async def ask_confirm(interaction: discord.Interaction, prompt: str, **view_kwargs) -> None:
    view = ConfirmView(requester_id=interaction.user.id, **view_kwargs)
    await interaction.response.send_message(prompt, ephemeral=True, view=view)
    view.prompt_interaction = interaction


# ---------- IPMI slash commands ----------
async def ipmi_reply(args: list[str]) -> str:
    return f"✅ `{await run_ipmi(args)}`"


@client.tree.command(name="status", description="Get chassis power status via IPMI")
async def status(interaction: discord.Interaction):
    if not await guard(interaction):
        return
    await respond_with(interaction, lambda: ipmi_reply(["chassis", "power", "status"]))


@client.tree.command(name="poweron", description="Power ON the server via IPMI")
async def poweron(interaction: discord.Interaction):
    if not await guard(interaction):
        return
    await respond_with(interaction, lambda: ipmi_reply(["chassis", "power", "on"]))


@client.tree.command(name="poweroff", description="Power OFF the server via IPMI (requires confirmation)")
async def poweroff(interaction: discord.Interaction):
    if not await guard(interaction):
        return
    await ask_confirm(
        interaction,
        "⚠️ **Confirm power off?** This will turn the server *off* immediately.",
        command="/poweroff",
        confirm_label="Confirm power off",
        working_text="🛑 Power off confirmed. Sending IPMI command…",
        action=lambda: ipmi_reply(["chassis", "power", "off"]),
    )


@client.tree.command(name="powercycle", description="Power cycle the server via IPMI")
async def powercycle(interaction: discord.Interaction):
    if not await guard(interaction):
        return
    await respond_with(interaction, lambda: ipmi_reply(["chassis", "power", "cycle"]))


# ---------- Docker slash commands (/docker ...) ----------
docker = app_commands.Group(name="docker", description="Control containers on the Docker server")


async def container_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not user_allowed(interaction):
        return []
    needle = current.lower()
    names = await container_names_for_autocomplete()
    return [app_commands.Choice(name=n, value=n) for n in names if needle in n.lower()][:25]


async def valid_container(interaction: discord.Interaction, name: str) -> bool:
    if CONTAINER_NAME_RE.fullmatch(name):
        return True
    await interaction.response.send_message("❌ That isn't a valid container name.", ephemeral=True)
    return False


@docker.command(name="list", description="Show the containers Wattson can control")
async def docker_list_cmd(interaction: discord.Interaction):
    if not await guard(interaction):
        return

    async def action() -> str:
        rows = await docker_list()
        if not rows:
            return "No containers are on the allowlist yet (/etc/wattson/containers on the Docker server)."
        return "\n".join(
            f"{STATE_EMOJI.get(state, '❔')} `{name}`: {state} ({info})" for name, state, info in rows
        )

    await respond_with(interaction, action)


@docker.command(name="start", description="Start a container")
@app_commands.describe(container="Container name")
@app_commands.autocomplete(container=container_autocomplete)
async def docker_start(interaction: discord.Interaction, container: str):
    if not await guard(interaction) or not await valid_container(interaction, container):
        return
    await respond_with(interaction, lambda: docker_action("start", container))


@docker.command(name="stop", description="Stop a container (requires confirmation)")
@app_commands.describe(container="Container name")
@app_commands.autocomplete(container=container_autocomplete)
async def docker_stop(interaction: discord.Interaction, container: str):
    if not await guard(interaction) or not await valid_container(interaction, container):
        return
    await ask_confirm(
        interaction,
        f"⚠️ **Stop `{container}`?** Anyone connected to it will be dropped.",
        command="/docker stop",
        confirm_label="Confirm stop",
        working_text=f"🛑 Stopping `{container}`…",
        action=lambda: docker_action("stop", container),
    )


@docker.command(name="restart", description="Restart a container (requires confirmation)")
@app_commands.describe(container="Container name")
@app_commands.autocomplete(container=container_autocomplete)
async def docker_restart(interaction: discord.Interaction, container: str):
    if not await guard(interaction) or not await valid_container(interaction, container):
        return
    await ask_confirm(
        interaction,
        f"⚠️ **Restart `{container}`?** Anyone connected to it will be dropped while it restarts.",
        command="/docker restart",
        confirm_label="Confirm restart",
        working_text=f"🔁 Restarting `{container}`…",
        action=lambda: docker_action("restart", container),
    )


async def reboot_reply() -> str:
    await run_ctl("reboot", timeout=30)
    return (
        "🔁 Reboot started. Containers with a restart policy will come back on their own "
        "once the server is up. Try `/docker list` in a few minutes."
    )


@docker.command(name="reboot", description="Reboot the Docker server (requires confirmation)")
async def docker_reboot(interaction: discord.Interaction):
    if not await guard(interaction):
        return
    await ask_confirm(
        interaction,
        "⚠️ **Reboot the Docker server?** Every container on it goes down until it comes back up.",
        command="/docker reboot",
        confirm_label="Confirm reboot",
        working_text="🔁 Sending reboot…",
        action=reboot_reply,
    )


if DOCKER_SSH_HOST:
    client.tree.add_command(docker)


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
