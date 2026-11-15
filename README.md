# Wattson

A Discord bot that runs as a Home Assistant app (add-on) on HAOS. It does two jobs:

- **IPMI power control** for a server with a BMC (for example a Supermicro board).
- **Docker control** for the always-on Docker server: list, start, stop, restart and update containers, or reboot the box. This goes over SSH with a key that can only run one locked-down script.

Every command replies privately (ephemeral), and only members with the configured Discord role can use them.

## Commands

| Command | What it does | Confirm? |
|---|---|---|
| `/status` | IPMI chassis power status | |
| `/poweron` | Power the IPMI server on | |
| `/poweroff` | Power the IPMI server off | Yes |
| `/powercycle` | Power cycle the IPMI server | |
| `/docker list` | Allowlisted containers and their state | |
| `/docker start <container>` | Start a container | |
| `/docker stop <container>` | Stop a container | Yes |
| `/docker restart <container>` | Restart a container | Yes |
| `/docker updates` | Check which containers have a newer image (downloads it, changes nothing) | |
| `/docker update <container>` | Pull the latest image and recreate the container if it changed | Yes |
| `/docker reboot` | Reboot the Docker server | Yes |

Container names autocomplete. The `/docker` commands only appear once `DOCKER_SSH_HOST` is set.

### Game updates

`/docker update` works for containers created with `docker compose`. It re-runs
`docker compose pull` and `up` for just that one service, using the compose file
and `.env` it was deployed from:

- If there's a new image, the container is recreated on it. A running game comes back up; a stopped game stays stopped.
- If the image is already current, nothing is touched.
- If the compose file was edited since the last deploy, the container is rebuilt from it.

How a game actually updates depends on how it's packaged:

- **Published image** (for example `factoriotools/factorio` or `itzg/minecraft-server`): a new image is the update, so `/docker update` handles it.
- **Self-updating on start** (many SteamCMD servers): the image rarely changes; the game downloads its update when it starts, so `/docker restart` is the update.
- **Pinned versions in a published image** (for example a modpack version set in compose): edit the version in the compose file on the server, then `/docker update` applies it.
- **Built locally with a pinned version**: see below.

### Version-tracked builds

If a game's image is built from your own Dockerfile and its version is a build
argument, Wattson can track it against an upstream version feed. Keep the
version in the project's `.env` and add three labels to the compose service:

```yaml
# compose.yaml
services:
  factorio:
    build:
      context: .
      args:
        FACTORIO_VERSION: "${FACTORIO_VERSION:?set FACTORIO_VERSION in .env}"
    labels:
      wattson.version-var: FACTORIO_VERSION                       # variable in .env
      wattson.version-url: https://factorio.com/api/latest-releases  # JSON feed
      wattson.version-path: stable.headless                        # where in the JSON
```

```bash
# .env
FACTORIO_VERSION=2.0.77
```

Then:

- `/docker updates` compares the `.env` version with the feed, for example `2.0.77 → 2.0.78`.
- `/docker update` writes the new version to `.env`, runs `docker compose build`, and recreates the container. If the build fails, the old version goes back into `.env` and the running game isn't touched.

The `wattson` user needs write access to that one `.env` file (see [server/SETUP.md](server/SETUP.md), step 3c). A locally built game without these labels shows as skipped.

## Repo layout

```
repository.yaml        Lets Home Assistant read this repo as an app repository
wattson/               The Home Assistant app
  config.yaml          App metadata and the settings shown in the Configuration tab
  Dockerfile           Builds the bot on the HA box (no prebuilt image)
  run.sh               Reads the settings, creates the SSH key, starts the bot
  app/bot.py           The bot
  app/requirements.txt
server/                Goes on the Docker server
  wattson-ctl          The only command Wattson's SSH key may run
  containers.example   Starter allowlist
  SETUP.md             Step-by-step server setup
```

## Install on Home Assistant

1. Go to **Settings > Apps > App store**, open the **⋮** menu, pick **Repositories** and add `https://github.com/bruceconklin/wattson`.
2. Install **Wattson Discord Bot**. Home Assistant builds it on the box from this repo.
3. Fill in the **Configuration** tab (below) and start it.

## Shipping a change

1. Bump `version` in `wattson/config.yaml`. Home Assistant only notices a change when the version goes up.
2. Commit and push to `main`.
3. With auto-update on, Home Assistant installs it on its next store check. To get it right away: **App store > ⋮ > Check for updates**, then **Update** on the app.

If `server/wattson-ctl` changed too, reinstall it on the Docker server (step 2 of [server/SETUP.md](server/SETUP.md)).

## Configuration

Set these in the app's **Configuration** tab. They never go in this repo.

| Option | Required | Notes |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Bot token from the Discord developer portal |
| `IPMI_HOST` | Yes | BMC address of the IPMI server |
| `IPMI_USER` / `IPMI_PASS` | Yes | BMC login |
| `DISCORD_GUILD_ID` | Yes | Your server's ID, so commands sync instantly |
| `DISCORD_ALLOWED_ROLE_ID` | Yes | Only members with this role can use Wattson |
| `DOCKER_SSH_HOST` | No | IP or hostname of the Docker server. Empty turns Docker control off |
| `DOCKER_SSH_USER` | No | SSH user on the Docker server, default `wattson` |

## Docker server setup

Follow [server/SETUP.md](server/SETUP.md). In short: a dedicated `wattson` user, the `wattson-ctl` script, an allowlist of containers, read-only access to your compose folders (for updates), a sudo rule for `systemctl reboot` only, and Wattson's public key, which the app prints in its log on start.

## Security notes

- Secrets live only in the app's Configuration tab. `.gitignore` blocks `.env` and `options.json` files as a backstop.
- The IPMI password is passed to `ipmitool` through an environment variable, not the command line.
- Wattson's SSH key is generated inside the app's `/data` folder and never leaves it. On the Docker server it is restricted to `wattson-ctl`, to logins from the Home Assistant box, and to no shell, port forwarding or TTY.
- `wattson-ctl` only touches containers on `/etc/wattson/containers`, validates names, and can reboot only through a sudo rule that allows nothing else.
- Updates only re-deploy compose files already on the Docker server, one service at a time. Nothing typed in Discord can introduce a new image or config.
- Every IPMI and SSH call has a timeout and runs without blocking the bot, so a slow server can't freeze it.
