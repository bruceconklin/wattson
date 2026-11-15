# Docker server setup (the always-on Docker host)

Wattson reaches the Docker server over SSH with its own key. That key can only
run `wattson-ctl`, which in turn can only list, start, stop, restart and update
the containers on an allowlist, or reboot the box. No shell, no other commands.

Run these on the Docker server from this `server/` folder.

**Updating an existing install:** when `wattson-ctl` changes in this repo,
reinstall it with step 2. Nothing else needs redoing.

## 1. Create a dedicated user

```bash
sudo useradd --create-home --shell /bin/bash --groups docker wattson
sudo usermod -p '*' wattson   # no password login; SSH keys still work
```

Being in the `docker` group is effectively root, which is why the SSH key in
step 5 is locked to `wattson-ctl` and to the Home Assistant box's IP.

## 2. Install the control script

From this folder:

```bash
sudo install -m 755 wattson-ctl /usr/local/bin/wattson-ctl
```

Or straight from GitHub:

```bash
curl -fsSL -o /tmp/wattson-ctl https://raw.githubusercontent.com/bruceconklin/wattson/main/server/wattson-ctl
sudo install -m 755 /tmp/wattson-ctl /usr/local/bin/wattson-ctl
```

## 3. Set the container allowlist

```bash
docker ps -a --format '{{.Names}}'          # see your real container names
sudo mkdir -p /etc/wattson
sudo install -m 644 containers.example /etc/wattson/containers
sudo nano /etc/wattson/containers           # keep only what Wattson may control
```

Use container **names**, not IDs; names survive `docker compose up` recreating
a container. Edits take effect immediately.

## 3b. Let Wattson read your compose files (for `/docker update`)

`/docker update` re-runs `docker compose pull` and `up` for one service, so the
`wattson` user needs read access to the compose folders (including `.env`
files). It only works for containers created with `docker compose`.

Find the folders your allowlisted containers came from:

```bash
docker ps -a --format '{{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}'
```

If they all live under one folder (for example `/home/YOU/games`), grant
read-only access to it with ACLs. Your files keep their owner and permissions:

```bash
GAMES=/home/YOU/games                                # the folder holding your compose projects
sudo apt install -y acl                              # only if setfacl is missing
sudo setfacl -m u:wattson:x "$(dirname "$GAMES")"    # pass through your home, without listing it
sudo setfacl -R -m u:wattson:rX "$GAMES"             # read existing compose files and .env files
sudo setfacl -R -d -m u:wattson:rX "$GAMES"          # and anything added later
```

If a game lives somewhere else, repeat the last three lines for that folder.

## 4. Allow the reboot (and nothing else) through sudo

```bash
command -v systemctl                        # expect /usr/bin/systemctl
echo 'wattson ALL=(root) NOPASSWD: /usr/bin/systemctl reboot' \
  | sudo tee /etc/sudoers.d/wattson >/dev/null
sudo chmod 440 /etc/sudoers.d/wattson
sudo visudo -c
```

Skip this step if you don't want reboots from Discord. `wattson-ctl reboot`
will then refuse with a clear error.

## 5. Authorize Wattson's key

The Wattson app creates its own SSH key on first start and prints the public
key in its log (Settings > Apps > Wattson Discord Bot > Log). Copy that line,
then:

```bash
sudo -u wattson mkdir -p -m 700 /home/wattson/.ssh
echo 'command="/usr/local/bin/wattson-ctl",from="HA_IP",restrict PASTE_PUBLIC_KEY_HERE' \
  | sudo -u wattson tee -a /home/wattson/.ssh/authorized_keys >/dev/null
sudo chmod 600 /home/wattson/.ssh/authorized_keys
```

Replace `HA_IP` with your Home Assistant box's LAN address and `PASTE_PUBLIC_KEY_HERE` with the key from the log. `from=` makes the key useless from any other machine.

## 6. Test locally

```bash
sudo -u wattson /usr/local/bin/wattson-ctl list
sudo -u wattson /usr/local/bin/wattson-ctl check                       # pulls images, changes nothing
sudo -u wattson /usr/local/bin/wattson-ctl stop not-a-real-container   # should refuse
```

If `check` works but `update` says the wattson user can't read a compose
folder or file, revisit step 3b.
