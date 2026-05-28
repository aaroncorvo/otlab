#!/usr/bin/env bash
# install-teacher-panel.sh — rsync the teacher/ tree to the Pi, build the
# Docker image, run the container with classroom-scoped env vars.
#
# Idempotent. Re-run after teacher/ changes to rebuild + restart.
#
# Usage:
#   ./scripts/install-teacher-panel.sh otadmin@<teacher-pi>.local
#
# Env overrides (sensible defaults for the production classroom — override
# for the smoke test if running on a different subnet):
#   SCAN_BASE       192.168.10              first 3 octets of classroom subnet
#   SCAN_START      100                     first host octet to scan
#   SCAN_END        120                     last host octet to scan
#   MAX_HOSTS       21                      auto-lock roster after N hosts
#   FORTI_IP        (empty)                 set to FortiGate IP if you have one
#   DASH_USER       otlab                   basic-auth user
#   DASH_PASS       P@ssw0rd!               basic-auth pass (rotate per event!)

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-teacher.local}"
RUNTIME_USER=otadmin
REMOTE_DIR="/home/${RUNTIME_USER}/teacher"

SCAN_BASE="${SCAN_BASE:-192.168.10}"
SCAN_START="${SCAN_START:-100}"
SCAN_END="${SCAN_END:-120}"
MAX_HOSTS="${MAX_HOSTS:-21}"
FORTI_IP="${FORTI_IP:-}"
DASH_USER="${DASH_USER:-otlab}"
DASH_PASS="${DASH_PASS:-P@ssw0rd!}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> installing OTLab Teacher Panel on $PI_HOST"
echo "    SCAN_BASE=$SCAN_BASE  range $SCAN_START-$SCAN_END  max=$MAX_HOSTS"
[ -n "$FORTI_IP" ] && echo "    FORTI_IP=$FORTI_IP (panel will show FortiGate card)"
echo

# Sanity check
ssh -o BatchMode=yes "$PI_HOST" 'sudo -n true 2>/dev/null' || {
    echo "ERROR: $PI_HOST needs passwordless sudo. Run bootstrap-users.sh first." >&2
    exit 1
}
ssh -o BatchMode=yes "$PI_HOST" 'command -v docker >/dev/null' || {
    echo "ERROR: Docker missing on $PI_HOST. Run bootstrap-teacher-pi.sh first." >&2
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Rsync teacher/ tree to the Pi
# ---------------------------------------------------------------------------
echo "==> rsync teacher/ to $REMOTE_DIR"
rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
    "$REPO_ROOT/teacher/" "${PI_HOST}:/tmp/otlab-teacher-stage/"
ssh "$PI_HOST" "
    sudo mkdir -p '$REMOTE_DIR'
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-teacher-stage/ '$REMOTE_DIR/'
    rm -rf /tmp/otlab-teacher-stage
"

# ---------------------------------------------------------------------------
# 2. Build the otlab-teacher Docker image on the Pi
# ---------------------------------------------------------------------------
echo "==> building otlab-teacher Docker image (~3-5 min first time)"
ssh "$PI_HOST" "
    cd '$REMOTE_DIR'
    docker build -t otlab-teacher -f Dockerfile . 2>&1 | tail -20
"

# ---------------------------------------------------------------------------
# 3. Stop any existing container, start the new one
# ---------------------------------------------------------------------------
echo "==> (re)starting otlab-teacher container"
ssh "$PI_HOST" "
    docker rm -f otlab-teacher 2>/dev/null || true
    docker volume create classroom-state >/dev/null

    docker run -d \\
        --name otlab-teacher \\
        --restart unless-stopped \\
        -p 8080:8080 \\
        -e SCAN_BASE='$SCAN_BASE' \\
        -e SCAN_START='$SCAN_START' \\
        -e SCAN_END='$SCAN_END' \\
        -e MAX_HOSTS='$MAX_HOSTS' \\
        -e DASH_USER='$DASH_USER' \\
        -e DASH_PASS='$DASH_PASS' \\
        $([ -n "$FORTI_IP" ] && echo "-e FORTI_IP='$FORTI_IP'") \\
        -v classroom-state:/var/lib/teacher \\
        otlab-teacher
"

# ---------------------------------------------------------------------------
# 4. Verify
# ---------------------------------------------------------------------------
echo "==> verifying"
sleep 3
ssh "$PI_HOST" "
    docker ps --filter name=otlab-teacher --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo '    sanity check (curl /api/status):'
    curl -fsS -u '$DASH_USER:$DASH_PASS' http://localhost:8080/api/status 2>&1 \\
        | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d.get(\"config\",{}); print(\"      scan range:\", c.get(\"base\"), c.get(\"start\"), \"-\", c.get(\"end\")); print(\"      fortigate:\", c.get(\"fortigate\",{}).get(\"enabled\"))' \\
        || echo '      (curl failed — check `docker logs otlab-teacher`)'
"

PI_IP=$(echo "$PI_HOST" | sed 's/.*@//')
echo
echo "==> done"
echo "    open: http://$PI_IP:8080/  (login: $DASH_USER / $DASH_PASS)"
echo
echo "    To view logs:  ssh $PI_HOST 'docker logs -f otlab-teacher'"
echo "    To rebuild:    re-run this script after editing teacher/"
