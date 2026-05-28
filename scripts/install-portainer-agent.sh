#!/usr/bin/env bash
# install-portainer-agent.sh — deploy Portainer Agent on a student Pi so
# the teacher's Portainer CE can manage that Pi's Docker remotely.
#
# Architecture: each student runs a tiny Portainer Agent container that
# exposes the Pi's Docker daemon over TCP/9001. The teacher's Portainer
# server (running on otlab-teacher:9443) is then told about each student
# as an "Environment" pointing at https://<student-ip>:9001 (TLS, mutual
# auth via shared agent-secret).
#
# Doesn't touch the OTLab fabric — just gives you a single UI to inspect
# every student's clab containers (otlab-fw-dmz-pcn, otlab-plc-1-virt,
# otlab-sensor-sim, etc.) without SSHing.
#
# Idempotent. Re-run pulls latest image + restarts the agent.
#
# Usage:
#   ./scripts/install-portainer-agent.sh otadmin@<student-pi>
#
# After this runs on a student, in the teacher's Portainer:
#   Environments → Add environment → Portainer Agent
#   Name:        student-N
#   Environment URL: https://<student-ip>:9001
#   Save
# Or use the AGENT_SECRET env (below) and add via API.

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-student-01.local}"
AGENT_PORT="${AGENT_PORT:-9001}"

echo "==> deploying Portainer Agent on $PI_HOST"

ssh -o BatchMode=yes "$PI_HOST" "sudo -n true 2>/dev/null" || {
    echo "ERROR: $PI_HOST needs passwordless sudo. Run bootstrap-users.sh first." >&2
    exit 1
}

ssh "$PI_HOST" "
    set -e
    sudo docker volume create portainer_agent_data >/dev/null 2>&1 || true
    sudo docker rm -f portainer-agent 2>/dev/null || true
    sudo docker run -d \\
        --name portainer-agent \\
        --restart=unless-stopped \\
        -p ${AGENT_PORT}:9001 \\
        -v /var/run/docker.sock:/var/run/docker.sock \\
        -v /var/lib/docker/volumes:/var/lib/docker/volumes \\
        -v portainer_agent_data:/data \\
        portainer/agent:latest 2>&1 | tail -3
    sleep 2
    sudo docker ps --filter name=portainer-agent --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'
"

PI_IP=$(echo "$PI_HOST" | sed 's/.*@//')
echo
echo "==> done. Add this endpoint in the teacher's Portainer:"
echo "    URL:  https://otlab-teacher:9443"
echo "    Path: Environments → Add environment → Portainer Agent"
echo "    Name: $(ssh "$PI_HOST" hostname 2>/dev/null || echo 'student-N')"
echo "    Environment URL: ${PI_IP}:${AGENT_PORT}"
echo "    Save. The Pi's containers will then appear in the Portainer UI."
