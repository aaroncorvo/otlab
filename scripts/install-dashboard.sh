#!/usr/bin/env bash
# install-dashboard.sh — deploy the OTLab status dashboard onto softplc-2.
#
# What it does:
#   1. rsyncs dashboard/ to /home/otuser/lab/dashboard/ (owned by otuser)
#   2. ensures Flask + Flask-HTTPAuth are in /home/otuser/lab/.venv-modern/
#   3. generates a self-signed TLS cert for the dashboard if missing
#   4. lays down /etc/sudoers.d/099_otuser_reboot (narrow NOPASSWD rule
#      so the dashboard can self-reboot softplc-2 without full sudo)
#   5. generates an ed25519 SSH keypair for otuser if missing, prints the
#      pubkey, and authorizes it as otadmin@<remote-pi> for every other Pi
#      so remote reboots work without password
#   6. installs + enables otlab-dashboard.service
#   7. prints the URL + credentials
#
# Idempotent — safe to re-run anytime (refreshes files + service).
#
# Usage:
#   ./scripts/install-dashboard.sh                                # default otadmin@RASPLC02.local
#   ./scripts/install-dashboard.sh otadmin@192.168.120.19
#
# Pre-reqs:
#   - softplc-2 has been through bootstrap-pi.sh (lab venv exists)
#   - bootstrap-users.sh has run on softplc-1, softplc-2, honeypot-host
#     (so otadmin exists on each, NOPASSWD sudo)

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing — backward-compatible.
#   ./install-dashboard.sh                         # default: deploy to softplc-2
#   ./install-dashboard.sh otadmin@host            # deploy to a specific host
#   ./install-dashboard.sh otadmin@host --target-host=ops-host
#                                                   # deploy to ops-host; the
#                                                   # remote-Pi pubkey list
#                                                   # adapts to include all
#                                                   # 3 PLC Pis (when ops-host
#                                                   # is the dashboard runtime
#                                                   # location, it must SSH
#                                                   # to all 3 for reboot/
#                                                   # restart orchestration).
# ---------------------------------------------------------------------------
TARGET_HOST_ROLE="softplc-2"   # default — back-compat
PI_HOST=""
for arg in "$@"; do
    case "$arg" in
        --target-host=*) TARGET_HOST_ROLE="${arg#*=}" ;;
        --target-host)    shift; TARGET_HOST_ROLE="$1" ;;
        *)                if [ -z "$PI_HOST" ]; then PI_HOST="$arg"; fi ;;
    esac
done
PI_HOST="${PI_HOST:-otadmin@RASPLC02.local}"

DASH_SRC="dashboard"
DASH_DST="/home/otuser/lab/dashboard"
RUNTIME_USER="otuser"

# Remote-Pi pubkey-distribution list depends on which host we're deploying
# the dashboard ON. The dashboard SSHes to OTHER Pis for reboot/restart, so
# the list is "all PLC Pis except the one we're deploying to".
case "$TARGET_HOST_ROLE" in
    softplc-2)
        # Default deployment — dashboard on softplc-2, SSH to softplc-1 + honeypot.
        REMOTE_PIS=(     "192.168.120.216" "192.168.120.48"  )  # mgmt IPs (laptop-reachable)
        REMOTE_LAB_IPS=( "10.20.30.47"     "10.20.30.48"     )  # lab IPs (dashboard-side)
        ;;
    ops-host)
        # Future deployment — dashboard on the L3 ops-host. SSH to all 3 PLCs.
        # The mgmt IPs are placeholder until the ops-host's IP is known; the
        # script attempts ssh-copy-id and falls through gracefully if a host
        # is unreachable from the laptop side.
        REMOTE_PIS=(     "192.168.120.216" "192.168.120.19"  "192.168.120.48"  )
        REMOTE_LAB_IPS=( "10.20.30.47"     "10.20.30.49"     "10.20.30.48"     )
        ;;
    *)
        echo "ERROR: --target-host must be 'softplc-2' or 'ops-host' (got: $TARGET_HOST_ROLE)" >&2
        exit 1
        ;;
esac

echo "==> deploying OTLab dashboard to $PI_HOST (target-host role: $TARGET_HOST_ROLE)"

# ---------------------------------------------------------------------------
# 1. Sync source tree
# ---------------------------------------------------------------------------
echo "==> rsyncing $DASH_SRC/ -> $PI_HOST:/tmp/dashboard-stage/"
rsync -a --delete "${DASH_SRC}/" "${PI_HOST}:/tmp/dashboard-stage/"

ssh "$PI_HOST" "
    set -e
    sudo -u ${RUNTIME_USER} mkdir -p ${DASH_DST}
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/dashboard-stage/ ${DASH_DST}/
    rm -rf /tmp/dashboard-stage
    # Don't ship dashboard.env from the repo example over the live one.
    # Test as otuser — otadmin can't traverse into /home/otuser (mode 700).
    if ! sudo -u ${RUNTIME_USER} test -f ${DASH_DST}/dashboard.env; then
        sudo install -m 0640 -o ${RUNTIME_USER} -g ${RUNTIME_USER} \
            ${DASH_DST}/dashboard.env.example ${DASH_DST}/dashboard.env
        echo '    laid down ${DASH_DST}/dashboard.env from example'
    else
        echo '    ${DASH_DST}/dashboard.env already exists — leaving alone'
    fi
"

# ---------------------------------------------------------------------------
# 2. venv deps (Flask + Flask-HTTPAuth — pymodbus already installed by
#    bootstrap-pi.sh into /home/otuser/lab/.venv-modern/)
# ---------------------------------------------------------------------------
echo "==> ensuring Flask + Flask-HTTPAuth in /home/otuser/lab/.venv-modern"
ssh "$PI_HOST" "
    sudo -u ${RUNTIME_USER} /home/${RUNTIME_USER}/lab/.venv-modern/bin/pip install --quiet \
        flask flask-httpauth
"

# ---------------------------------------------------------------------------
# 3. Self-signed TLS cert for the dashboard
# ---------------------------------------------------------------------------
echo "==> ensuring self-signed TLS cert exists"
ssh "$PI_HOST" "
    set -e
    if ! sudo -u ${RUNTIME_USER} test -f ${DASH_DST}/cert.pem \
       || ! sudo -u ${RUNTIME_USER} test -f ${DASH_DST}/key.pem; then
        sudo -u ${RUNTIME_USER} openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout ${DASH_DST}/key.pem \
            -out ${DASH_DST}/cert.pem \
            -days 3650 \
            -subj '/CN=otlab-dashboard/O=Maple Ridge Treatment Plant/OU=ICS Village' \
            -addext 'subjectAltName=DNS:RASPLC02.local,DNS:rasplc02,DNS:localhost,IP:127.0.0.1,IP:192.168.120.19,IP:10.20.30.49,IP:100.77.255.56' \
            >/dev/null 2>&1
        sudo chmod 600 ${DASH_DST}/key.pem
        sudo chmod 644 ${DASH_DST}/cert.pem
        echo '    generated cert.pem + key.pem (10 yr, self-signed)'
    else
        echo '    cert + key already exist — skipping'
    fi
"

# ---------------------------------------------------------------------------
# 4. Sudoers drop-in: narrow NOPASSWD for self-reboot only
# ---------------------------------------------------------------------------
echo "==> sudoers rule for otuser self-reboot + tcpdump"
ssh "$PI_HOST" "
    sudo tee /etc/sudoers.d/099_otuser_reboot >/dev/null <<EOF
# OTLab dashboard runtime (otuser) needs to:
#   1. self-reboot softplc-2 (from the dashboard's Reboot button)
#   2. run tcpdump for the dashboard's pcap-capture feature
#   3. timeout(1) wraps tcpdump for fixed-duration captures
#   4. restart specific services (granular alternative to full reboot)
otuser ALL=(ALL) NOPASSWD: /bin/systemctl reboot, /usr/bin/systemctl reboot, /usr/bin/tcpdump, /usr/bin/timeout, /bin/systemctl restart sensor-sim, /bin/systemctl restart openplc, /bin/systemctl restart otlab-dashboard, /usr/bin/systemctl restart sensor-sim, /usr/bin/systemctl restart openplc, /usr/bin/systemctl restart otlab-dashboard
EOF
    sudo chmod 440 /etc/sudoers.d/099_otuser_reboot
    echo '    /etc/sudoers.d/099_otuser_reboot installed'
"

echo "==> ensuring captures + ssh-cm directories exist"
ssh "$PI_HOST" "
    sudo -u ${RUNTIME_USER} mkdir -p ${DASH_DST}/captures ${DASH_DST}/.ssh-cm
    sudo -u ${RUNTIME_USER} chmod 750 ${DASH_DST}/captures
    sudo -u ${RUNTIME_USER} chmod 700 ${DASH_DST}/.ssh-cm
    echo '    ${DASH_DST}/captures + .ssh-cm ready'
"

# ---------------------------------------------------------------------------
# 5. SSH keypair for otuser, plus authorize on remote otadmin accounts
# ---------------------------------------------------------------------------
echo "==> ensuring otuser has an SSH keypair for remote reboots"
PUBKEY=$(ssh "$PI_HOST" "
    set -e
    # Run the existence test as otuser — otadmin can't see into otuser's
    # mode-700 ~/.ssh, so a plain '[ -f ... ]' here always returns false
    # and we'd re-run ssh-keygen every time, which then prompts to
    # overwrite and silently fails.
    if ! sudo -u ${RUNTIME_USER} test -f /home/${RUNTIME_USER}/.ssh/id_ed25519; then
        sudo -u ${RUNTIME_USER} mkdir -p /home/${RUNTIME_USER}/.ssh
        sudo -u ${RUNTIME_USER} chmod 700 /home/${RUNTIME_USER}/.ssh
        sudo -u ${RUNTIME_USER} ssh-keygen -t ed25519 -N '' \
            -C 'otuser@softplc-2 dashboard reboot key' \
            -f /home/${RUNTIME_USER}/.ssh/id_ed25519 >/dev/null
    fi
    sudo cat /home/${RUNTIME_USER}/.ssh/id_ed25519.pub
")

echo "    pubkey: $PUBKEY"

# Stage the pubkey on the local host so we can ssh-copy-id it to remotes.
TMP_PUB="$(mktemp)"
echo "$PUBKEY" > "$TMP_PUB"

# For each remote Pi, append the pubkey to otadmin's authorized_keys (idempotent).
for remote in "${REMOTE_PIS[@]}"; do
    echo "==> authorizing otuser@softplc-2 -> otadmin@${remote}"
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "otadmin@${remote}" true 2>/dev/null; then
        ssh "otadmin@${remote}" "
            mkdir -p ~/.ssh && chmod 700 ~/.ssh
            grep -qF '$PUBKEY' ~/.ssh/authorized_keys 2>/dev/null \
                || echo '$PUBKEY' >> ~/.ssh/authorized_keys
            chmod 600 ~/.ssh/authorized_keys
        "
        echo "    ${remote} authorized"
    else
        echo "    WARN: cannot reach otadmin@${remote} — skipping (reboot from dashboard for that host will fail until you authorize manually)"
    fi
done

# Pre-warm known_hosts for both remotes from softplc-2's otuser side, so
# ssh from the dashboard doesn't trip on host-key prompts. Scan both the
# mgmt and the lab IPs since the dashboard reboot path uses the lab IPs
# (most reliable from softplc-2).
ssh "$PI_HOST" "
    sudo -u ${RUNTIME_USER} bash -c '
        for h in ${REMOTE_PIS[@]} ${REMOTE_LAB_IPS[@]}; do
            ssh-keyscan -t ed25519 -T 5 \"\$h\" 2>/dev/null >> /home/${RUNTIME_USER}/.ssh/known_hosts || true
        done
        sort -u /home/${RUNTIME_USER}/.ssh/known_hosts -o /home/${RUNTIME_USER}/.ssh/known_hosts
    '
"

rm -f "$TMP_PUB"

# ---------------------------------------------------------------------------
# 6. systemd unit
# ---------------------------------------------------------------------------
echo "==> installing systemd unit + (re)starting service"
ssh "$PI_HOST" "
    sudo install -m 0644 ${DASH_DST}/otlab-dashboard.service \
        /etc/systemd/system/otlab-dashboard.service
    sudo systemctl daemon-reload
    sudo systemctl enable --quiet otlab-dashboard
    sudo systemctl restart otlab-dashboard
"

sleep 2
echo "==> status"
ssh "$PI_HOST" 'sudo systemctl status otlab-dashboard --no-pager 2>&1 | head -12'
echo
echo "==> recent journal"
ssh "$PI_HOST" 'sudo journalctl -u otlab-dashboard -n 5 --no-pager 2>&1' || true

# ---------------------------------------------------------------------------
# 7a. Stamp /etc/otlab-bootstrap-info for the dashboard's last-bootstrap card.
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
# 7. Summary
# ---------------------------------------------------------------------------
HOST_BARE="${PI_HOST##*@}"
# Use sudo grep — dashboard.env is mode 0640 owned by otuser, otadmin can't
# read it directly across the mode-700 home dir.
DASH_USER_VAL=$(ssh "$PI_HOST" "sudo grep '^DASH_USER=' ${DASH_DST}/dashboard.env | cut -d= -f2")
DASH_PASS_VAL=$(ssh "$PI_HOST" "sudo grep '^DASH_PASS=' ${DASH_DST}/dashboard.env | cut -d= -f2")

cat <<EOF

==============================================================================
 OTLab Dashboard deployed.

   URL:    https://${HOST_BARE}:8000/   (also try https://192.168.120.19:8000/)
   user:   ${DASH_USER_VAL}
   pass:   ${DASH_PASS_VAL}

 The cert is self-signed — your browser will warn. Click through.

 Logs:    ssh ${PI_HOST} 'sudo journalctl -u otlab-dashboard -f'
 Restart: ssh ${PI_HOST} 'sudo systemctl restart otlab-dashboard'

 Edit ${DASH_DST}/dashboard.env on the Pi to rotate the password,
 then restart the service.
==============================================================================
EOF
