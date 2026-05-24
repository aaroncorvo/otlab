#!/usr/bin/env bash
# install-student-promtail.sh — install Promtail on a student Pi so it
# ships Suricata + dashboard + firewall + journal logs to the teacher
# SIEM at /etc/otlab/student.env:SIEM_LOKI_URL.
#
# Renders teacher/agents/promtail-student.yml.tmpl with the per-Pi
# values, installs a systemd service, starts it.
#
# Usage:
#   ./teacher/agents/install-student-promtail.sh otadmin@otlab-student-05.local
#
# Pre-reqs:
#   - otlab-install.sh has run on the Pi (so /etc/otlab/student.env exists)
#   - Pi has internet for the Promtail binary download (or set PROMTAIL_BIN_URL)
#
# Idempotent.

set -euo pipefail

PI_HOST="${1:?Usage: $0 otadmin@<pi>.local}"
PROMTAIL_VERSION="${PROMTAIL_VERSION:-2.9.4}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMPL="$REPO_ROOT/teacher/agents/promtail-student.yml.tmpl"

[[ -f "$TMPL" ]] || { echo "ERROR: template missing: $TMPL" >&2; exit 1; }

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# ── pull per-Pi config from the Pi ────────────────────────────────────
say "reading /etc/otlab/student.env from $PI_HOST"
ENV_CONTENT="$(ssh "$PI_HOST" 'cat /etc/otlab/student.env')"
if [[ -z "$ENV_CONTENT" ]]; then
    echo "ERROR: /etc/otlab/student.env missing — run otlab-install.sh first" >&2
    exit 1
fi

STUDENT_ID=$(echo "$ENV_CONTENT" | grep -E '^STUDENT_ID='      | cut -d= -f2 || true)
HOSTNAME_NEW=$(echo "$ENV_CONTENT" | grep -E '^STUDENT_HOSTNAME=' | cut -d= -f2 || true)
TEACHER_IP=$(echo "$ENV_CONTENT" | grep -E '^TEACHER_IP='      | cut -d= -f2 || true)
LOKI_URL=$(echo "$ENV_CONTENT" | grep -E '^SIEM_LOKI_URL='     | cut -d= -f2 || true)

if [[ -z "$STUDENT_ID" || -z "$LOKI_URL" ]]; then
    echo "ERROR: student.env missing STUDENT_ID or SIEM_LOKI_URL" >&2
    exit 1
fi

echo "    student #$STUDENT_ID, ships to $LOKI_URL"

# ── render the template locally ───────────────────────────────────────
say "rendering promtail config"
RENDERED="$(mktemp)"
sed \
    -e "s|__STUDENT_ID__|$STUDENT_ID|g" \
    -e "s|__STUDENT_HOSTNAME__|$HOSTNAME_NEW|g" \
    -e "s|__TEACHER_IP__|$TEACHER_IP|g" \
    -e "s|__SIEM_LOKI_URL__|$LOKI_URL|g" \
    "$TMPL" > "$RENDERED"

# ── install promtail binary on the Pi ─────────────────────────────────
say "installing promtail $PROMTAIL_VERSION binary (arm64)"
ssh "$PI_HOST" "
set -e
if ! command -v promtail >/dev/null 2>&1; then
    cd /tmp
    ARCH=\$(dpkg --print-architecture)   # arm64 on Pi 4/5
    curl -fsSL -o promtail.zip \
        https://github.com/grafana/loki/releases/download/v${PROMTAIL_VERSION}/promtail-linux-\${ARCH}.zip
    unzip -o promtail.zip
    sudo mv promtail-linux-\${ARCH} /usr/local/bin/promtail
    sudo chmod +x /usr/local/bin/promtail
    rm -f promtail.zip
fi
sudo mkdir -p /etc/promtail /var/lib/promtail
"

# ── copy rendered config ─────────────────────────────────────────────
say "deploying rendered config to /etc/promtail/promtail-otlab.yml"
scp -q "$RENDERED" "$PI_HOST:/tmp/promtail-otlab.yml"
ssh "$PI_HOST" "
sudo install -m 0644 -o root -g root /tmp/promtail-otlab.yml /etc/promtail/promtail-otlab.yml
rm /tmp/promtail-otlab.yml
"
rm -f "$RENDERED"

# ── install systemd unit ──────────────────────────────────────────────
say "installing systemd unit promtail-otlab.service"
ssh "$PI_HOST" '
sudo tee /etc/systemd/system/promtail-otlab.service >/dev/null <<UNIT
[Unit]
Description=OTLab Promtail (ships logs to teacher SIEM)
Documentation=https://grafana.com/docs/loki/latest/clients/promtail/
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/promtail -config.file=/etc/promtail/promtail-otlab.yml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now promtail-otlab.service
'

# ── verify ────────────────────────────────────────────────────────────
say "verifying"
sleep 2
ssh "$PI_HOST" 'sudo systemctl is-active promtail-otlab.service' || {
    echo "WARN: service not active — check 'journalctl -u promtail-otlab' on the Pi" >&2
    exit 1
}
echo "    promtail-otlab.service is active"
echo "    Verify in Grafana within ~30s — student-$STUDENT_ID logs should appear under {student_id=\"$STUDENT_ID\"}"
