#!/usr/bin/env bash
# bootstrap-pi.sh — provision a fresh Raspberry Pi OS Lite install with
# everything the OTLab needs: apt deps, raspi-config flags, OpenPLC v3,
# the lab Python venv. Idempotent — safe to re-run.
#
# Assumes:
# - Pi OS Lite (Bookworm or Trixie) installed
# - SSH key auth set up to PI_HOST (run `ssh-copy-id <user>@<host>` once)
# - The Pi is on a network with internet access (apt + git + pip need it)
#
# Pre-req: scripts/bootstrap-users.sh has run against this Pi to create
# otadmin (NOPASSWD sudo) and otuser (non-sudo).
#
# Usage:
#   ./scripts/bootstrap-pi.sh PI_HOST
#
# Args:
#   PI_HOST   user@host, default otadmin@<host>.local. Pass user@host to override
#             (e.g. for live Pis where the original user is still in use).
#
# Run this once per Pi. After it succeeds:
#   - For softplc-1 / softplc-2: run bootstrap-openplc-role.sh
#   - For honeypot-host: run scripts/deploy-honeypot.sh (or scp ./honeypot/
#     to the Pi and `docker compose up -d` per honeypot/README.md)
#
# Take ~15-20 min (most of it OpenPLC's compile of matiec + the runtime).

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@RASPLC01.local}"

echo "==> bootstrapping Pi at $PI_HOST"
echo "    (will install apt packages, raspi-config, OpenPLC, and a lab venv)"
echo

# Sanity check — we have key auth and the user has sudo. Fail early otherwise.
ssh -o BatchMode=yes "$PI_HOST" 'sudo -n true 2>/dev/null' || {
    echo "ERROR: $PI_HOST needs passwordless sudo for this script."
    echo "  Edit /etc/sudoers (or /etc/sudoers.d/...) to grant NOPASSWD,"
    echo "  or run the script's commands manually one at a time."
    exit 1
}

# ---------------------------------------------------------------------------
# 1. apt: update + install everything we need
# ---------------------------------------------------------------------------

echo "==> apt update + install base packages (~3 min)"
ssh "$PI_HOST" '
    set -e
    sudo apt-get update -qq
    # Pre-seed wireshark-common so its postinst creates the "wireshark" group
    # and grants packet-capture rights to its members. Without this it falls
    # back to noninteractive and skips the group entirely, breaking the
    # usermod -aG step below.
    echo "wireshark-common wireshark-common/install-setuid boolean true" | \
        sudo debconf-set-selections
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        git \
        vim \
        htop \
        tmux \
        tcpdump \
        wireshark-common \
        net-tools \
        python3-pip \
        python3-venv \
        curl \
        jq \
        sqlite3 \
        snmp \
        build-essential
    # Belt-and-suspenders: if for any reason the group still does not exist
    # (e.g. wireshark-common was already installed before our seed landed),
    # create it so the usermod step downstream can succeed idempotently.
    getent group wireshark >/dev/null || sudo groupadd --system wireshark
'

# ---------------------------------------------------------------------------
# 1b. Disable cloud-init.
#     Pi Imager (newer versions) seeds a NoCloud cloud-init user-data file
#     on /boot/firmware that re-applies hostname + rewrites /etc/hosts on
#     every boot, walking over any manual changes. By the time bootstrap-pi
#     runs, Pi Imager's first-boot config has already been applied, so we
#     don't need cloud-init for anything else — disabling it gives the lab
#     full ownership of /etc/hostname, /etc/hosts, etc. Idempotent.
# ---------------------------------------------------------------------------
echo "==> disabling cloud-init (Pi Imager's first-boot is done; we own the box now)"
ssh "$PI_HOST" '
    if [ -d /etc/cloud ] && command -v cloud-init >/dev/null 2>&1; then
        sudo touch /etc/cloud/cloud-init.disabled
        for svc in cloud-init cloud-init-local cloud-config cloud-final; do
            sudo systemctl mask --quiet "$svc" 2>/dev/null || true
        done
        echo "    /etc/cloud/cloud-init.disabled created; services masked"
    else
        echo "    cloud-init not present — skipping"
    fi
'

# ---------------------------------------------------------------------------
# 2. raspi-config: enable I2C, SPI, hardware UART; disable serial console
# ---------------------------------------------------------------------------

echo "==> enabling I2C / SPI / hardware UART via raspi-config"
ssh "$PI_HOST" '
    sudo raspi-config nonint do_i2c 0
    sudo raspi-config nonint do_spi 0
    sudo raspi-config nonint do_serial_hw 0
    sudo raspi-config nonint do_serial_cons 1
'

# ---------------------------------------------------------------------------
# 3. group memberships for the user
# ---------------------------------------------------------------------------

echo "==> adding otadmin + otuser to dialout / gpio / i2c / spi / video / wireshark / adm groups"
ssh "$PI_HOST" '
    for u in otadmin otuser; do
        if id "$u" >/dev/null 2>&1; then
            # video group is required for vcgencmd to read /dev/vcio
            # (the dashboard reads SoC temp via vcgencmd measure_temp).
            # adm group lets the user read /var/log/journal without sudo,
            # so the dashboard health probe can surface failed-SSH counts
            # and other journalctl-derived metrics.
            sudo usermod -aG dialout,gpio,i2c,spi,video,wireshark,adm "$u"
        fi
    done
'

# ---------------------------------------------------------------------------
# 4. OpenPLC v3 — clone + compile if not already present
# ---------------------------------------------------------------------------

echo "==> OpenPLC v3 install (~10-15 min on a fresh Pi)"
ssh "$PI_HOST" '
    set -e
    if [ -d ~/OpenPLC_v3 ] && [ -x ~/OpenPLC_v3/start_openplc.sh ]; then
        echo "    OpenPLC already installed at ~/OpenPLC_v3 — skipping clone+compile"
    else
        cd ~
        git clone --depth=1 https://github.com/thiagoralves/OpenPLC_v3.git
        cd OpenPLC_v3
        ./install.sh rpi
    fi
'

# ---------------------------------------------------------------------------
# 5. Lab Python venv with modern pymodbus 3.x
# ---------------------------------------------------------------------------

echo "==> creating /home/otuser/lab/.venv-modern with pymodbus 3.x + paho-mqtt + pyserial + requests"
ssh "$PI_HOST" '
    set -e
    sudo -u otuser mkdir -p /home/otuser/lab
    if [ ! -d /home/otuser/lab/.venv-modern ]; then
        sudo -u otuser python3 -m venv /home/otuser/lab/.venv-modern
    fi
    sudo -u otuser /home/otuser/lab/.venv-modern/bin/pip install --quiet --upgrade pip
    sudo -u otuser /home/otuser/lab/.venv-modern/bin/pip install --quiet \
        "pymodbus>=3" paho-mqtt pyserial requests
'

# ---------------------------------------------------------------------------
# 6. Drop the eth0 default-route hack into bashrc as a comment so future
#    Aaron remembers it (the lab DHCP advertises a useless gateway on eth0)
# ---------------------------------------------------------------------------

echo "==> adding bashrc note about the eth0 default-route fix (otadmin + otuser)"
ssh "$PI_HOST" '
    for u in otadmin otuser; do
        bashrc=/home/$u/.bashrc
        if [ -f "$bashrc" ] && ! sudo grep -q "OTLab eth0 fix" "$bashrc"; then
            sudo tee -a "$bashrc" >/dev/null <<EOF

# OTLab eth0 fix: the lab DHCP advertises 10.20.30.1 as default gateway but
# nothing routes outbound there. wlan0 has the real default route. Drop the
# bogus eth0 default whenever you need apt/pip/git over the internet:
#
#   sudo ip route del default via 10.20.30.1 dev eth0
EOF
            sudo chown $u:$u "$bashrc"
        fi
    done
'

# ---------------------------------------------------------------------------
# Stamp /etc/otlab-bootstrap-info so the dashboard can show last-bootstrap
# metadata. Idempotent — overwritten each run.
# ---------------------------------------------------------------------------
COMMIT="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$(basename "$0")"
ssh "$PI_HOST" "
sudo tee /etc/otlab-bootstrap-info >/dev/null <<EOF
ts=$TS
commit=$COMMIT
script=$SCRIPT
EOF
sudo chmod 644 /etc/otlab-bootstrap-info
"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo
echo "==> bootstrap complete on $PI_HOST"
echo
echo "Next:"
echo "  - For softplc-1 / softplc-2:"
echo "      ./scripts/bootstrap-openplc-role.sh $PI_HOST <softplc-1|softplc-2>"
echo "  - For softplc-2's sensor-sim service:"
echo "      ./scripts/install-sensor-sim.sh $PI_HOST"
