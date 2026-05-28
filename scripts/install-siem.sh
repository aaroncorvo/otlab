#!/usr/bin/env bash
# install-siem.sh — deploy the Loki + Grafana + Promtail SIEM stack on the
# teacher Pi. Rsyncs teacher/siem/, runs `docker compose up -d`.
#
# Idempotent. Re-run to apply config changes (Loki/Grafana/Promtail configs
# or new Grafana dashboards in teacher/siem/grafana/dashboards/).
#
# Usage:
#   ./scripts/install-siem.sh otadmin@<teacher-pi>.local
#
# Env overrides:
#   GRAFANA_ADMIN_USER   admin
#   GRAFANA_ADMIN_PASS   P@ssw0rd!     rotate per event

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-teacher.local}"
RUNTIME_USER=otadmin
REMOTE_DIR="/home/${RUNTIME_USER}/teacher/siem"

GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
GRAFANA_ADMIN_PASS="${GRAFANA_ADMIN_PASS:-P@ssw0rd!}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> installing classroom SIEM (Loki + Grafana + Promtail) on $PI_HOST"

# Sanity checks
ssh -o BatchMode=yes "$PI_HOST" 'sudo -n true 2>/dev/null' || {
    echo "ERROR: $PI_HOST needs passwordless sudo. Run bootstrap-users.sh first." >&2
    exit 1
}
ssh -o BatchMode=yes "$PI_HOST" 'command -v docker >/dev/null' || {
    echo "ERROR: Docker missing. Run bootstrap-teacher-pi.sh first." >&2
    exit 1
}
ssh -o BatchMode=yes "$PI_HOST" 'docker compose version >/dev/null 2>&1' || {
    echo "ERROR: 'docker compose' (v2 plugin) missing. Re-run bootstrap-teacher-pi.sh." >&2
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Rsync teacher/siem/ to the Pi
# ---------------------------------------------------------------------------
echo "==> rsync teacher/siem/ to $REMOTE_DIR"
rsync -a --delete \
    "$REPO_ROOT/teacher/siem/" "${PI_HOST}:/tmp/otlab-siem-stage/"
ssh "$PI_HOST" "
    sudo mkdir -p '$REMOTE_DIR'
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-siem-stage/ '$REMOTE_DIR/'
    rm -rf /tmp/otlab-siem-stage
"

# ---------------------------------------------------------------------------
# 2. docker compose up (pulls images first time — Loki + Grafana + Promtail)
# ---------------------------------------------------------------------------
echo "==> docker compose up -d (~3-5 min first time for image pulls)"
ssh "$PI_HOST" "
    cd '$REMOTE_DIR'
    GRAFANA_ADMIN_USER='$GRAFANA_ADMIN_USER' \\
    GRAFANA_ADMIN_PASS='$GRAFANA_ADMIN_PASS' \\
    docker compose up -d
"

# ---------------------------------------------------------------------------
# 3. Verify
# ---------------------------------------------------------------------------
echo "==> verifying"
sleep 5
ssh "$PI_HOST" "
    cd '$REMOTE_DIR'
    docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo '    Loki ready check:'
    for i in 1 2 3 4 5; do
        if curl -fsS http://localhost:3100/ready 2>/dev/null | grep -q ready; then
            echo '      Loki:    ready'
            break
        fi
        sleep 3
    done
    echo '    Grafana ready check:'
    for i in 1 2 3 4 5; do
        if curl -fsS http://localhost:3000/api/health 2>/dev/null | grep -q ok; then
            echo '      Grafana: ok'
            break
        fi
        sleep 3
    done
"

PI_IP=$(echo "$PI_HOST" | sed 's/.*@//')
echo
echo "==> done"
echo "    Grafana: http://$PI_IP:3000  (login: $GRAFANA_ADMIN_USER / $GRAFANA_ADMIN_PASS)"
echo "    Loki:    http://$PI_IP:3100/ready"
echo
echo "    Default dashboard: OTLab → Classroom Overview"
echo "    Logs will arrive once student Pis run install-student-promtail.sh"
