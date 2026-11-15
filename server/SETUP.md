# Docker server setup (the always-on Docker host)

Wattson reaches the Docker server over SSH with its own key. That key can only
run `wattson-ctl`, which in turn can only list, start, stop and restart the
containers on an allowlist, or reboot the box. No shell, no other commands.

Run these on the Docker server from this `server/` folder.

## 1. Create a dedicated user

```bash
sudo useradd --create-home --shell /bin/bash --groups docker wattson
sudo usermod -p '*' wattson   # no password login; SSH keys still work
```

Being in the `docker` group is effectively root, which is why the SSH key in
step 5 is locked to `wattson-ctl` and to the Home Assistant box's IP.

## 2. Install the control script

```bash
sudo install -m 755 wattson-ctl /usr/local/bin/wattson-ctl
```

## 3. Set the container allowlist

```bash
docker ps -a --format '{{.Names}}'          # see your real container names
sudo mkdir -p /etc/wattson
sudo install -m 644 containers.example /etc/wattson/containers
sudo nano /etc/wattson/containers           # keep only what Wattson may control
```

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
sudo -u wattson /usr/local/bin/wattson-ctl stop not-a-real-container   # should refuse
```
