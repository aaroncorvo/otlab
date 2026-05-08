#!/usr/bin/env bash
# bootstrap-users.sh — create the lab's canonical otadmin + otuser accounts
# on a Pi. Idempotent — safe to re-run any time.
#
# Run this FIRST against a fresh Pi (or any Pi missing the lab users), using
# whatever SSH user already exists (typical for a Pi Imager-installed system:
# the username you set during imaging, with NOPASSWD sudo).
#
# After this completes:
#   - otadmin exists, has NOPASSWD sudo, has SSH key auth from this laptop
#   - otuser exists, no sudo, has SSH key auth from this laptop
#   - Subsequent bootstrap-* scripts can run against otadmin@<host>.local
#
# Pre-reqs:
#   - Pi OS Lite installed
#   - SSH key auth set up to PI_HOST: `ssh-copy-id <user>@<host>` once
#   - The existing user has NOPASSWD sudo (default on Pi OS for the user
#     created during Pi Imager setup)
#
# Usage:
#   ./scripts/bootstrap-users.sh <existing-user>@<host>
#
# e.g. ./scripts/bootstrap-users.sh pi@RASPLC03.local

set -euo pipefail

PI_HOST="${1:?<existing-user>@<host> required, e.g. pi@RASPLC03.local}"
HOST="${PI_HOST##*@}"

# Local public key that both new users should accept for login from this laptop
LOCAL_PUBKEY_FILE="$HOME/.ssh/id_ed25519.pub"
test -f "$LOCAL_PUBKEY_FILE" || {
    echo "ERROR: $LOCAL_PUBKEY_FILE missing — generate with ssh-keygen -t ed25519"
    exit 1
}
LOCAL_PUBKEY="$(cat "$LOCAL_PUBKEY_FILE")"

ssh -o BatchMode=yes "$PI_HOST" 'sudo -n true 2>/dev/null' || {
    cat <<EOF
ERROR: $PI_HOST does not have passwordless sudo for the existing user.
SSH in manually and run (it will prompt once for your password):

    USER=\$(whoami)
    echo "\$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/099_\${USER}_nopasswd
    sudo chmod 440 /etc/sudoers.d/099_\${USER}_nopasswd

Then re-run this bootstrap script.
EOF
    exit 1
}

echo "==> bootstrapping lab users (otadmin + otuser) on $PI_HOST"

# ---------------------------------------------------------------------------
# 1. Create the two users (idempotent)
# ---------------------------------------------------------------------------
echo "==> ensuring otadmin + otuser exist"
ssh "$PI_HOST" '
    for u in otadmin otuser; do
        if id "$u" >/dev/null 2>&1; then
            echo "    $u already exists (uid=$(id -u $u))"
        else
            sudo useradd -m -s /bin/bash -c "OTLab $u" "$u"
            echo "    created $u (uid=$(id -u $u), home=/home/$u)"
        fi
    done
'

# ---------------------------------------------------------------------------
# 2. NOPASSWD sudo for otadmin
# ---------------------------------------------------------------------------
echo "==> NOPASSWD sudo for otadmin"
ssh "$PI_HOST" '
    sudo tee /etc/sudoers.d/099_otadmin_nopasswd >/dev/null <<EOF
otadmin ALL=(ALL) NOPASSWD: ALL
EOF
    sudo chmod 440 /etc/sudoers.d/099_otadmin_nopasswd
    echo "    /etc/sudoers.d/099_otadmin_nopasswd installed"
'

# ---------------------------------------------------------------------------
# 2b. Strip any sudo rights from otuser. The canonical model is otuser =
#     non-sudo. If Pi Imager happened to create the initial user as
#     "otuser" (the OS imaging step prompts for a username), it will have
#     written a NOPASSWD sudoers file and added otuser to the sudo group,
#     which we have to undo to bring it to canonical state. Idempotent —
#     no-op if otuser is already non-sudo.
# ---------------------------------------------------------------------------
echo "==> ensuring otuser is non-sudo (canonical model)"
ssh "$PI_HOST" '
    # Remove any sudoers.d drop-ins that mention otuser (catches the
    # Pi-Imager-created /etc/sudoers.d/010_pi-nopasswd-style file as well
    # as our own 099_otuser_nopasswd if it ever existed).
    if sudo grep -lE "^otuser\b" /etc/sudoers.d/* 2>/dev/null | grep -q . ; then
        for f in $(sudo grep -lE "^otuser\b" /etc/sudoers.d/* 2>/dev/null); do
            echo "    removing $f (granted otuser sudo rights)"
            sudo rm -f "$f"
        done
    fi
    # Remove otuser from the sudo / wheel groups if present.
    for g in sudo wheel; do
        if id -nG otuser 2>/dev/null | tr " " "\n" | grep -qx "$g"; then
            echo "    removing otuser from $g group"
            sudo gpasswd -d otuser "$g" >/dev/null
        fi
    done
'

# ---------------------------------------------------------------------------
# 2c. Disable cloud-init.
#     Pi Imager (newer) seeds NoCloud cloud-init user-data on /boot/firmware
#     that re-applies hostname + rewrites /etc/hosts on every boot. By the
#     time bootstrap-users.sh runs, first-boot config is done — disabling
#     cloud-init hands /etc/hostname and /etc/hosts back to us. Idempotent.
#     Also runs as a safety net in bootstrap-pi.sh + bootstrap-honeypot.sh
#     in case bootstrap-users isn't the first script someone runs.
# ---------------------------------------------------------------------------
echo "==> disabling cloud-init (Pi Imager's first-boot is done; lab takes the box now)"
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
# 2d. Disable wifi powersave on every wlan0 NetworkManager connection.
#     The Pi 3 B+ radio aggressively sleeps and silently drops inbound
#     ARP/ICMP, making the Pi unreachable from a wifi-only host even
#     though wlan0 has a valid DHCP lease. Pi 5 is less affected but
#     we've seen 200ms+ RTT spikes after reboot — fix is harmless there.
#     Idempotent: nmcli modify is a no-op if already set, and
#     reapply doesn't drop the link.
# ---------------------------------------------------------------------------
echo "==> disabling wifi powersave on any wlan0 NM connection"
ssh "$PI_HOST" '
    if ! command -v nmcli >/dev/null 2>&1; then
        echo "    NetworkManager not present — skipping"
        exit 0
    fi
    set -e
    for c in $(nmcli -t -f NAME,TYPE connection show | awk -F: "/:802-11-wireless\$/ {print \$1}"); do
        # nmcli reports the value back as the human-readable label
        # (\"disable\" / \"enable\" / \"default\" / \"ignore\") not the
        # integer code, so compare against \"disable\".
        cur=$(nmcli -t -f 802-11-wireless.powersave connection show "$c" 2>/dev/null | cut -d: -f2 | xargs)
        if [ "$cur" != "disable" ]; then
            echo "    setting wifi.powersave=disable on \"$c\" (was: $cur)"
            sudo nmcli connection modify "$c" wifi.powersave 2
        else
            echo "    \"$c\" already powersave=disable"
        fi
    done
    sudo nmcli device reapply wlan0 2>/dev/null || true
'

# ---------------------------------------------------------------------------
# 3. SSH key auth from this laptop, for both users
# ---------------------------------------------------------------------------
echo "==> SSH key auth (laptop -> otadmin/otuser)"
ssh "$PI_HOST" "
    set -e
    PUBKEY='$LOCAL_PUBKEY'
    for u in otadmin otuser; do
        sudo -u \"\$u\" mkdir -p /home/\$u/.ssh
        echo \"\$PUBKEY\" | sudo tee /home/\$u/.ssh/authorized_keys >/dev/null
        sudo chown -R \$u:\$u /home/\$u/.ssh
        sudo chmod 700 /home/\$u/.ssh
        sudo chmod 600 /home/\$u/.ssh/authorized_keys
    done
    echo '    authorized_keys installed for otadmin and otuser'
"

# ---------------------------------------------------------------------------
# 4. Update local known_hosts so direct mDNS connects don't prompt
# ---------------------------------------------------------------------------
ssh-keyscan -t ed25519,ecdsa,rsa "$HOST" 2>/dev/null >> ~/.ssh/known_hosts || true

# ---------------------------------------------------------------------------
# 5. Verify
# ---------------------------------------------------------------------------
echo "==> verifying"
ssh -o BatchMode=yes "otadmin@$HOST" 'echo "    otadmin login OK as $(whoami); sudo gives $(sudo -n whoami)"'
ssh -o BatchMode=yes "otuser@$HOST"  'echo "    otuser login OK as $(whoami); sudo would prompt (correct)"'

# ---------------------------------------------------------------------------
# 6. Stamp /etc/otlab-bootstrap-info so the dashboard can show "this Pi was
#    bootstrapped X ago at commit Y by script Z". Idempotent — overwritten
#    on every run.
# ---------------------------------------------------------------------------
COMMIT="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$(basename "$0")"
ssh -o BatchMode=yes "otadmin@$HOST" "
sudo tee /etc/otlab-bootstrap-info >/dev/null <<EOF
ts=$TS
commit=$COMMIT
script=$SCRIPT
EOF
sudo chmod 644 /etc/otlab-bootstrap-info
"

echo
echo "==> done. Continue with:"
echo "    ./scripts/bootstrap-pi.sh                  otadmin@$HOST"
echo "    ./scripts/bootstrap-openplc-role.sh        otadmin@$HOST <softplc-1|softplc-2>"
echo "    ./scripts/install-sensor-sim.sh            otadmin@$HOST    # softplc-2 only"
echo "    ./scripts/bootstrap-honeypot.sh            otadmin@$HOST    # honeypot-host only"
