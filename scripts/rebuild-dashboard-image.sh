#!/usr/bin/env bash
# rebuild-dashboard-image.sh — rebuild otlab/dashboard:latest on a Pi from
# the current dashboard/ source, so dashboard code changes survive a clab
# redeploy / cohort reset.
#
# This does NOT recreate the running container (that's clab-managed and
# would drop its fabric veths). For a live in-place update use:
#     docker cp dashboard.py clab-otlab-dashboard:/opt/otlab/dashboard/...
#     docker restart clab-otlab-dashboard
# This script just refreshes the IMAGE so the next redeploy carries the code.
#
# Usage:
#   ./scripts/rebuild-dashboard-image.sh otadmin@10.20.30.49
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.49}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> staging build context to $PI_HOST"
ssh "$PI_HOST" 'rm -rf /tmp/otlab-dash-build && mkdir -p /tmp/otlab-dash-build/dashboard'
rsync -a --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'captures' --exclude 'audit.db' --exclude '.ssh-cm' \
    "$ROOT/dashboard/" "$PI_HOST:/tmp/otlab-dash-build/dashboard/"
rsync -a "$ROOT/virtual/dockerfiles/dashboard/Dockerfile" \
    "$PI_HOST:/tmp/otlab-dash-build/Dockerfile"

echo "==> docker build otlab/dashboard:latest"
ssh "$PI_HOST" 'cd /tmp/otlab-dash-build && docker build -q -t otlab/dashboard:latest -f Dockerfile . && rm -rf /tmp/otlab-dash-build && echo "  image rebuilt: $(docker images otlab/dashboard:latest --format "{{.ID}} {{.CreatedSince}}")"'

echo "==> done. Live container unchanged; next clab redeploy uses the new image."
