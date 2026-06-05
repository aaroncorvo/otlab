#!/usr/bin/env bash
# redeploy-teacher.sh — rebuild the teacher panel image from the current
# repo and recreate the container, preserving its state volume and env.
#
# The panel bakes teacher.py into the image (COPY . /opt/teacher/), so code
# changes (e.g. Links Hub edits) need a rebuild + recreate. The
# classroom-state volume (roster, SSH keys, layout) survives untouched.
#
# Usage:
#   ./teacher/redeploy-teacher.sh otadmin@10.20.30.27
#
# Env knobs match the running container; override on the command line if a
# given cohort differs:
#   ESP32_IPS, TEACHER_IPS, HONEYPOT_IPS, SCAN_BASE, SCAN_START, SCAN_END,
#   MAX_HOSTS, LISTEN_PORT
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.27}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Defaults captured from the live teacher container; override via env.
ESP32_IPS="${ESP32_IPS:-teacher=10.20.30.201,student-1=10.20.30.202,student-2=10.20.30.203}"
TEACHER_IPS="${TEACHER_IPS:-10.20.30.27,100.77.2.22}"
HONEYPOT_IPS="${HONEYPOT_IPS:-10.20.30.48}"
SCAN_BASE="${SCAN_BASE:-10.20.30}"
SCAN_START="${SCAN_START:-1}"
SCAN_END="${SCAN_END:-254}"
MAX_HOSTS="${MAX_HOSTS:-21}"
LISTEN_PORT="${LISTEN_PORT:-8080}"

echo "==> syncing teacher/ build context to $PI_HOST"
ssh "$PI_HOST" 'rm -rf /tmp/otlab-teacher-src && mkdir -p /tmp/otlab-teacher-src'
rsync -a --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$HERE/" "$PI_HOST:/tmp/otlab-teacher-src/"

echo "==> rebuilding image + recreating container (docker; no sudo needed)"
ssh "$PI_HOST" "bash -s" <<REMOTE
set -e
cd /tmp/otlab-teacher-src
docker build -t otlab-teacher -f Dockerfile .
docker stop otlab-teacher 2>/dev/null || true
docker rm   otlab-teacher 2>/dev/null || true
docker run -d --name otlab-teacher \
    --network host --restart unless-stopped \
    -v classroom-state:/var/lib/teacher \
    -e ESP32_IPS="$ESP32_IPS" \
    -e TEACHER_IPS="$TEACHER_IPS" \
    -e HONEYPOT_IPS="$HONEYPOT_IPS" \
    -e SCAN_BASE="$SCAN_BASE" \
    -e SCAN_START="$SCAN_START" \
    -e SCAN_END="$SCAN_END" \
    -e MAX_HOSTS="$MAX_HOSTS" \
    -e LISTEN_PORT="$LISTEN_PORT" \
    otlab-teacher
rm -rf /tmp/otlab-teacher-src
sleep 3
echo "container: \$(docker ps --filter name=otlab-teacher --format '{{.Status}}')"
REMOTE

echo
echo "==> done. Teacher panel: http://${PI_HOST#*@}:8080/  (otlab / P@ssw0rd!)"
