#!/usr/bin/env bash
# install-student-loki.sh — deploy a local Loki on a student Pi.
#
# Each student Pi runs its own Loki at http://localhost:3100 alongside
# the teacher's shared Loki. Promtail ships every log line to BOTH:
#
#     [student suricata + docker + journal]
#               │
#               ▼
#         Promtail (dual clients)
#         ├────► http://<student>:3100  (LOCAL  — data stays on the Pi)
#         └────► http://<teacher>:3100  (REMOTE — instructor's classroom view)
#
# Why both:
#   • Local: student takes the Pi home + queries their own data via
#     `logcli` or `curl` (or installs Grafana on their laptop pointed at
#     this Loki). Works fully offline, indefinite retention controlled
#     locally.
#   • Remote: instructor's Grafana shows the classroom-wide aggregated
#     view across all students.
#
# Idempotent. Re-running just pulls latest image + restarts the container.
#
# Usage:
#   ./scripts/install-student-loki.sh otadmin@<student-pi>
#
# After it's running, a student can query their own data:
#   curl -s 'http://localhost:3100/loki/api/v1/query?query={job="suricata"}&limit=5'
#   logcli --addr http://localhost:3100 query '{job="suricata", event_type="alert"}'

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-student-01.local}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> deploying local Loki on $PI_HOST"

ssh -o BatchMode=yes "$PI_HOST" "sudo -n true 2>/dev/null" || {
    echo "ERROR: $PI_HOST needs passwordless sudo. Run bootstrap-users.sh first." >&2
    exit 1
}
ssh -o BatchMode=yes "$PI_HOST" 'command -v docker >/dev/null' || {
    echo "ERROR: Docker missing on $PI_HOST. Run bootstrap-l3-mon-role.sh or otlab-install.sh first." >&2
    exit 1
}

# Stage the loki config from the repo onto the Pi (we reuse the teacher's
# loki-config.yml — same retention/storage settings).
echo "==> staging loki config"
scp -q "$REPO_ROOT/teacher/siem/loki/loki-config.yml" "$PI_HOST:/tmp/loki-config.yml"
ssh "$PI_HOST" "
    sudo mkdir -p /etc/otlab/loki
    sudo install -m 0644 -o root -g root /tmp/loki-config.yml /etc/otlab/loki/loki-config.yml
    rm -f /tmp/loki-config.yml
"

# Run Loki as a single container, named otlab-local-loki to distinguish
# from the teacher's otlab-siem-loki.
echo "==> (re)starting otlab-local-loki container"
ssh "$PI_HOST" '
    sudo docker volume create otlab-local-loki-data >/dev/null 2>&1 || true
    sudo docker rm -f otlab-local-loki 2>/dev/null || true
    sudo docker run -d \
        --name otlab-local-loki \
        --restart=unless-stopped \
        -p 127.0.0.1:3100:3100 \
        -v /etc/otlab/loki/loki-config.yml:/etc/loki/loki-config.yml:ro \
        -v otlab-local-loki-data:/loki \
        --log-opt max-size=10m --log-opt max-file=3 \
        grafana/loki:2.9.4 \
        -config.file=/etc/loki/loki-config.yml 2>&1 | tail -3
'

# Sanity check
echo "==> waiting for local Loki to become ready..."
ssh "$PI_HOST" '
    for i in $(seq 1 30); do
        if curl -fsS http://localhost:3100/ready 2>/dev/null | grep -q ready; then
            echo "    ready"
            break
        fi
        sleep 2
    done
    docker ps --filter name=otlab-local-loki --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
'

cat <<EOF

==> done. Local Loki running at http://localhost:3100/ready on $PI_HOST

For the student to query their own data later:

  # via curl + jq
  ssh otadmin@<pi> 'curl -s "http://localhost:3100/loki/api/v1/query?query=%7Bjob%3D%22suricata%22%7D&limit=5" | jq'

  # via logcli (Grafana's CLI — recommended; install with `apt install logcli` or pull binary)
  ssh otadmin@<pi> 'logcli --addr http://localhost:3100 query "{job=\"suricata\", event_type=\"alert\"}"'

  # for a UI: install Grafana on the student's laptop (NOT on the Pi),
  # add datasource pointing at http://<pi>:3100, then drop the same
  # dashboards from teacher/siem/grafana/dashboards/ into Grafana.
  # The student gets the same instructor view, but for THEIR data only.

Promtail still needs to be updated to dual-ship (local + teacher). Run:
  ./teacher/agents/install-student-promtail.sh $PI_HOST
(if promtail was already installed; the updated template will re-render
the config with both clients.)
EOF
