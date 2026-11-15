# Wattson

A Discord bot that runs as a Home Assistant app (add-on) on HAOS. It does two jobs:

- **IPMI power control** for a server with a BMC (for example a Supermicro board).
- **Docker control** for the always-on Docker server: list, start, stop and restart containers, or reboot the box. This goes over SSH with a key that can only run one locked-down script.

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
| `/docker reboot` | Reboot the Docker server | Yes |

Container names autocomplete. The `/docker` commands only appear once `DOCKER_SSH_HOST` is set.

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

## Install or update on Home Assistant

Wattson runs as a **local** app, so this repo can stay private.

1. Turn on the Samba share app and connect to the `addons` share from your Mac.
2. Copy the contents of `wattson/` into the Wattson folder on that share, replacing what's there.
3. In Home Assistant, go to **Settings > Apps > App store**, open the **⋮** menu and pick **Check for updates**.
4. Open **Wattson Discord Bot** and click **Update**. This rebuilds it from the new files.

If you change code without bumping `version` in `config.yaml`, use **Rebuild** in step 4 instead.

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

Follow [server/SETUP.md](server/SETUP.md). In short: a dedicated `wattson` user, the `wattson-ctl` script, an allowlist of containers, a sudo rule for `systemctl reboot` only, and Wattson's public key, which the app prints in its log on start.

## Security notes

- Secrets live only in the app's Configuration tab. `.gitignore` blocks `.env` and `options.json` files as a backstop.
- The IPMI password is passed to `ipmitool` through an environment variable, not the command line.
- Wattson's SSH key is generated inside the app's `/data` folder and never leaves it. On the Docker server it is restricted to `wattson-ctl`, to logins from the Home Assistant box, and to no shell, port forwarding or TTY.
- `wattson-ctl` only touches containers on `/etc/wattson/containers`, validates names, and can reboot only through a sudo rule that allows nothing else.
- Every IPMI and SSH call has a timeout and runs without blocking the bot, so a slow server can't freeze it.
