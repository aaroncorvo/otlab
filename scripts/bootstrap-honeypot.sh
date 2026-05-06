#!/usr/bin/env bash
# bootstrap-honeypot.sh — fresh Pi OS to running 3-persona Conpot fabric.
# Idempotent — re-running rsyncs any local changes and brings the stack
# back to canonical state without disturbing healthy containers.
#
# Pre-reqs:
#   - Pi OS Lite (Bookworm or Trixie) installed on the target Pi
#   - Pi has internet access (apt + Docker Hub need it; the lab segment's
#     DHCP-advertised default route gets removed automatically below)
#   - SSH key auth set up to PI_HOST: `ssh-copy-id <user>@<host>`
#   - Passwordless sudo for the user (default Pi OS setup)
#
# Usage:
#   ./scripts/bootstrap-honeypot.sh PI_HOST
#
# Args:
#   PI_HOST   user@host, e.g. otadmin@honeypot-host.local
#
# Run from the repo root (the script rsyncs the honeypot/ directory).
#
# Time: ~3-5 min on a fresh Pi (Docker install + 310 MB Conpot image pull).
# Idempotent re-run on a healthy stack: ~5 s, no container restarts unless
# files in honeypot/ changed since last deploy.

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@honeypot-host.local}"

# Repo-root-relative path to the honeypot tree we mirror to the Pi.
HONEYPOT_LOCAL_DIR="honeypot"
HONEYPOT_REMOTE_DIR='~/conpot/compose'

# UID/GID Conpot inside the container runs as. Log files have to be
# owned by this on the host filesystem or Conpot can't write them.
CONPOT_UID=2000
CONPOT_GID=2000

# Docker Compose v2 plugin: not in apt, so we grab the official binary.
# Pinned to "latest" — works on arm64 (Pi) and amd64.
COMPOSE_BIN_URL='https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64'

echo "==> bootstrapping honeypot fabric on $PI_HOST"

# Sanity checks — fail early if the Pi side isn't ready.
test -d "$HONEYPOT_LOCAL_DIR" || {
    echo "ERROR: $HONEYPOT_LOCAL_DIR/ not found. Run from the repo root."
    exit 1
}
ssh -o BatchMode=yes "$PI_HOST" 'sudo -n true 2>/dev/null' || {
    cat <<EOF
ERROR: $PI_HOST does not have passwordless sudo configured.

This script can't prompt for a sudo password over a non-interactive SSH
session. To grant the user passwordless sudo (lab convention — fine for
a teaching environment, would be inappropriate for production), SSH in
manually and run:

    USER=\$(whoami)
    echo "\$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/099_\${USER}_nopasswd
    sudo chmod 440 /etc/sudoers.d/099_\${USER}_nopasswd

Then re-run this bootstrap script.
EOF
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Drop the bogus eth0 default route the lab DHCP server hands out.
#    Without this, apt-get and curl fail to reach anything outbound because
#    the lab segment doesn't actually have a real upstream gateway.
# ---------------------------------------------------------------------------
ssh "$PI_HOST" 'sudo ip route del default via 10.20.30.1 dev eth0 2>/dev/null || true'

# ---------------------------------------------------------------------------
# 2. Install Docker engine if not already present.
# ---------------------------------------------------------------------------
echo "==> Docker engine"
ssh "$PI_HOST" '
    set -e
    if command -v docker >/dev/null; then
        echo "    docker $(docker --version | cut -d, -f1) already installed — skipping"
    else
        sudo apt-get update -qq
        sudo apt-get install -y -qq docker.io
    fi
'

# ---------------------------------------------------------------------------
# 3. Install Docker Compose v2 plugin if not present.
#    Not in Debian's apt repos as of Bookworm/Trixie, so we drop the binary
#    into Docker's CLI plugin directory directly.
# ---------------------------------------------------------------------------
echo "==> Docker Compose v2"
ssh "$PI_HOST" "
    set -e
    if docker compose version >/dev/null 2>&1; then
        echo \"    docker compose \$(docker compose version --short) already installed — skipping\"
    else
        sudo mkdir -p /usr/local/lib/docker/cli-plugins
        sudo curl -sSL '$COMPOSE_BIN_URL' -o /usr/local/lib/docker/cli-plugins/docker-compose
        sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
        echo \"    installed: \$(docker compose version --short)\"
    fi
"

# ---------------------------------------------------------------------------
# 4. Add user to docker group (idempotent, takes effect on next login).
#    The script itself uses `sudo docker compose` below to sidestep the
#    needs-relogin issue on first run.
# ---------------------------------------------------------------------------
echo "==> ensuring otadmin + otuser are in docker group"
ssh "$PI_HOST" '
    for u in otadmin otuser; do
        if id "$u" >/dev/null 2>&1; then
            if id -nG "$u" | grep -qw docker; then
                echo "    $u already in docker group"
            else
                sudo usermod -aG docker "$u"
                echo "    added $u (effective on next login)"
            fi
        fi
    done
'

# ---------------------------------------------------------------------------
# 5. rsync the honeypot/ tree to the Pi.
#    Excludes:
#      logs/         runtime forensic capture, regenerated on first up
#      .DS_Store     macOS cruft
#    --delete keeps the remote in sync with what's in the repo (any rogue
#    files added on the Pi side get cleaned up). Templates and configs
#    overwrite cleanly. logs/ is preserved because it's excluded.
# ---------------------------------------------------------------------------
echo "==> rsyncing $HONEYPOT_LOCAL_DIR/ to $PI_HOST:$HONEYPOT_REMOTE_DIR/"
ssh "$PI_HOST" "mkdir -p $HONEYPOT_REMOTE_DIR"
rsync -az --delete \
    --exclude='logs/' \
    --exclude='.DS_Store' \
    "$HONEYPOT_LOCAL_DIR/" "$PI_HOST:$HONEYPOT_REMOTE_DIR/"

# ---------------------------------------------------------------------------
# 6. Create per-persona log directories with the right UID/GID.
#    Conpot inside the container runs as UID 2000 and writes to /tmp
#    (which we bind-mount from these host directories).
# ---------------------------------------------------------------------------
echo "==> ensuring log dirs exist + owned by ${CONPOT_UID}:${CONPOT_GID}"
ssh "$PI_HOST" "
    cd $HONEYPOT_REMOTE_DIR
    mkdir -p logs/siemens logs/schneider logs/allenbradley
    sudo chown -R ${CONPOT_UID}:${CONPOT_GID} logs/
"

# ---------------------------------------------------------------------------
# 7. Bring the stack up. `docker compose up -d` is idempotent — if all
#    containers are already running the canonical config, it's a no-op.
#    If we just rsynced changes, it recreates affected containers.
#    `sudo` because the user may not be in the docker group yet on first
#    deploy (group only effective after relogin).
# ---------------------------------------------------------------------------
echo "==> docker compose up -d"
ssh "$PI_HOST" "cd $HONEYPOT_REMOTE_DIR && sudo docker compose up -d 2>&1 | tail -10"

# ---------------------------------------------------------------------------
# 8. Verify.
# ---------------------------------------------------------------------------
sleep 6
echo
echo "==> container status"
ssh "$PI_HOST" "cd $HONEYPOT_REMOTE_DIR && sudo docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || sudo docker compose ps"

echo
echo "==> bootstrap complete on $PI_HOST"
echo
echo "Verify cross-Pi from softplc-1 or softplc-2:"
echo "  snmpwalk -v2c -c public 10.20.30.50 1.3.6.1.2.1.1.5.0   # PS4-CPU01 (Siemens)"
echo "  snmpwalk -v2c -c public 10.20.30.51 1.3.6.1.2.1.1.5.0   # HVAC-M340 (Schneider)"
echo "  snmpwalk -v2c -c public 10.20.30.52 1.3.6.1.2.1.1.5.0   # CHEM-LGX01 (Rockwell)"
echo
echo "(macvlan caveat: cannot probe these IPs from $PI_HOST itself —"
echo " that's a Linux kernel limitation, not a misconfiguration.)"
