#!/usr/bin/env bash
# bootstrap-teacher-pi.sh — slim bootstrap for the teacher Pi.
#
# Companion to bootstrap-pi.sh (which is sized for student Pis — compiles
# OpenPLC, installs the lab venv). The teacher Pi only needs:
#   - Docker engine (for teacher panel + SIEM stack)
#   - Basic CLI tools (git, curl, jq)
#   - Hostname pinned to otlab-teacher
#   - cloud-init disabled (already done by bootstrap-users.sh)
#
# Skips: OpenPLC compile, raspi-config (I2C/SPI/UART), the lab Python venv,
# wireshark group setup — none needed on the teacher.
#
# Usage:
#   ./scripts/bootstrap-teacher-pi.sh otadmin@<teacher-pi>.local
#
# Idempotent. Safe to re-run.
# Time: ~5 minutes first run (apt + docker), ~5 seconds re-run.

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-teacher.local}"
NEW_HOSTNAME="${TEACHER_HOSTNAME:-otlab-teacher}"

echo "==> bootstrapping teacher Pi at $PI_HOST"
echo "    target hostname: $NEW_HOSTNAME"
echo

# Sanity check
ssh -o BatchMode=yes "$PI_HOST" 'sudo -n true 2>/dev/null' || {
    echo "ERROR: $PI_HOST needs passwordless sudo. Run bootstrap-users.sh first." >&2
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Set hostname
# ---------------------------------------------------------------------------
echo "==> setting hostname to $NEW_HOSTNAME"
ssh "$PI_HOST" "
    set -e
    current=\$(hostname)
    if [ \"\$current\" = \"$NEW_HOSTNAME\" ]; then
        echo '    already $NEW_HOSTNAME'
    else
        sudo hostnamectl set-hostname '$NEW_HOSTNAME'
        sudo sed -i.bak \"s/127.0.1.1.*/127.0.1.1\\t$NEW_HOSTNAME/\" /etc/hosts || \
            echo \"127.0.1.1 $NEW_HOSTNAME\" | sudo tee -a /etc/hosts
        echo \"    renamed: \$current -> $NEW_HOSTNAME\"
    fi
"

# ---------------------------------------------------------------------------
# 2. apt: base packages + Docker
# ---------------------------------------------------------------------------
echo "==> apt update + base packages (~2-3 min)"
ssh "$PI_HOST" '
    set -e
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        git vim htop tmux tcpdump net-tools \
        curl jq sqlite3 \
        ca-certificates gnupg lsb-release
'

# ---------------------------------------------------------------------------
# 3. Docker engine + Compose plugin
# ---------------------------------------------------------------------------
echo "==> installing Docker engine + Compose plugin"
ssh "$PI_HOST" '
    set -e
    if ! command -v docker >/dev/null 2>&1; then
        # Use Debian/Pi OS docker.io package — simpler than Docker CE repo
        sudo apt-get install -y -qq docker.io
    fi
    # Docker Compose v2 (plugin form: `docker compose ...`)
    # Trixie/Bookworm apt do not ship the official compose-plugin under a
    # consistent name. Try apt first, fall back to the GitHub binary which
    # is the canonical source.
    if ! docker compose version >/dev/null 2>&1; then
        sudo apt-get install -y -qq docker-compose-v2 2>/dev/null || \
            sudo apt-get install -y -qq docker-compose-plugin 2>/dev/null || true
    fi
    if ! docker compose version >/dev/null 2>&1; then
        echo "    apt has no compose-plugin; fetching binary from github.com/docker/compose..."
        ARCH=$(uname -m)
        COMPOSE_VERSION=v2.29.7
        sudo mkdir -p /usr/local/lib/docker/cli-plugins
        sudo curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
            "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}"
        sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    fi
    sudo systemctl enable --now docker
    # Allow otadmin to run docker without sudo
    sudo usermod -aG docker otadmin || true
    docker --version
    docker compose version
'

# ---------------------------------------------------------------------------
# 4. Bootstrap-info stamp (tells dashboard / panel how the box was built)
# ---------------------------------------------------------------------------
COMMIT="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$(basename "$0")"
ssh "$PI_HOST" "
sudo tee /etc/otlab-bootstrap-info >/dev/null <<EOF
ts=$TS
commit=$COMMIT
script=$SCRIPT
role=teacher
hostname=$NEW_HOSTNAME
EOF
sudo chmod 644 /etc/otlab-bootstrap-info
"

echo
echo "==> done"
echo "    Pi is now ready for:"
echo "      ./scripts/install-teacher-panel.sh otadmin@$NEW_HOSTNAME.local"
echo "      ./scripts/install-siem.sh          otadmin@$NEW_HOSTNAME.local"
echo
echo "    NOTE: otadmin was added to the docker group. If otadmin was"
echo "    already logged in, log out + back in for it to take effect."
