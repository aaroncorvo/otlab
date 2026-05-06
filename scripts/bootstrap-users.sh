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

echo
echo "==> done. Continue with:"
echo "    ./scripts/bootstrap-pi.sh                  otadmin@$HOST"
echo "    ./scripts/bootstrap-openplc-role.sh        otadmin@$HOST <softplc-1|softplc-2>"
echo "    ./scripts/install-sensor-sim.sh            otadmin@$HOST    # softplc-2 only"
echo "    ./scripts/bootstrap-honeypot.sh            otadmin@$HOST    # honeypot-host only"
