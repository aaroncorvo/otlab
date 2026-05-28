#!/usr/bin/env bash
# finalize-teacher-pi.sh — Tier-1 production-readiness items for the teacher Pi.
#
# Run AFTER otlab-install.sh's teacher chain (bootstrap-users +
# bootstrap-teacher-pi + install-teacher-panel + install-siem) has
# succeeded.  Idempotent — safe to re-run.
#
# What this does:
#   1. Verify NTP / time sync is running
#   2. Install the Tailscale binary (you run `sudo tailscale up` after,
#      to do the one-shot OAuth — script can't do that interactively)
#   3. Configure Docker daemon log rotation (10MB × 3 files per container)
#   4. Verify the teacher panel's /api/teacher/pubkey endpoint responds
#   5. Strip NOPASSWD sudo from the original Pi-Imager user (iadmin or pi)
#      so only otadmin has elevated rights — canonical lab posture
#   6. Stamp /etc/otlab-bootstrap-info with the finalize step recorded
#
# What this does NOT do (intentionally — needs your input):
#   - Rotate any of the default passwords (otadmin / otlab panel / admin
#     Grafana). Do that interactively at event time; see "Credentials"
#     in the OTLab Notion index.
#   - Configure the 8 PoE switch ports (poe0-poe7) — Tier 2 design
#     decision: bridge to mgmt? OT-extension? mirror? Defer until you
#     know which.
#   - Set up nftables — Tier 2 hygiene, not blocking for the bench.
#
# Usage:
#   ./scripts/finalize-teacher-pi.sh otadmin@<teacher-pi>.local
#
# Env:
#   IMAGER_USER   user to strip sudo from (default: iadmin; could be 'pi')

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-teacher.local}"
IMAGER_USER="${IMAGER_USER:-iadmin}"

echo "==> finalize-teacher-pi on $PI_HOST"
echo

# ── upload remote worker script (keeps 'sudo' off the local cmdline) ─
WORKER=$(mktemp)
cat >"$WORKER" <<'WORKER_EOF'
#!/usr/bin/env bash
set -euo pipefail
IMAGER_USER=$1

echo
echo "── 1. NTP / time sync ──────────────────────────────────────────"
if systemctl is-active --quiet systemd-timesyncd; then
    echo "  systemd-timesyncd: active"
    timedatectl status | grep -E "System clock synchronized|NTP service|Time zone"
elif systemctl is-active --quiet chrony; then
    echo "  chrony: active"
    chronyc tracking 2>/dev/null | head -5
else
    echo "  WARN: no NTP service detected — installing systemd-timesyncd"
    sudo apt-get install -y -qq systemd-timesyncd
    sudo systemctl enable --now systemd-timesyncd
fi

echo
echo "── 2. Tailscale binary install ─────────────────────────────────"
if command -v tailscale >/dev/null 2>&1; then
    echo "  tailscale already installed: $(tailscale version | head -1)"
else
    curl -fsSL https://tailscale.com/install.sh | sudo sh 2>&1 | tail -3
fi
if tailscale status >/dev/null 2>&1; then
    echo "  tailscale already enrolled: $(tailscale ip -4 2>/dev/null | head -1)"
else
    echo "  tailscale installed but NOT enrolled in a tailnet."
    echo "  To enroll, run on the Pi:"
    echo "    sudo tailscale up --hostname=otlab-teacher --ssh"
    echo "  (or with subnet routes:  --advertise-routes=192.168.10.0/24)"
fi

echo
echo "── 3. Docker daemon log rotation ───────────────────────────────"
DAEMON_JSON=/etc/docker/daemon.json
EXPECTED='{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}'
if [ -f "$DAEMON_JSON" ] && grep -q '"max-size"' "$DAEMON_JSON" 2>/dev/null; then
    echo "  /etc/docker/daemon.json already has max-size configured"
    sudo cat "$DAEMON_JSON"
else
    echo "  writing /etc/docker/daemon.json with 10MB × 3 rotation"
    echo "$EXPECTED" | sudo tee "$DAEMON_JSON" >/dev/null
    sudo systemctl restart docker
    sleep 3
    echo "  docker restarted; containers should auto-restart via restart-policy"
    docker ps --format 'table {{.Names}}\t{{.Status}}'
    echo
    echo "  NOTE: Docker only applies max-size to NEW containers. To"
    echo "  apply the 10MB/3-file rotation to the existing 4 containers,"
    echo "  re-run from the laptop:"
    echo "    ./scripts/install-teacher-panel.sh otadmin@<pi>"
    echo "    ./scripts/install-siem.sh otadmin@<pi>"
    echo "  (both are idempotent — they'll recreate the containers with"
    echo "   the new log-rotation defaults)"
fi

echo
echo "── 4. Verify teacher panel /api/teacher/pubkey ─────────────────"
PUBKEY_JSON=$(curl -fsS -u otlab:'P@ssw0rd!' http://localhost:8080/api/teacher/pubkey 2>/dev/null || true)
if echo "$PUBKEY_JSON" | grep -q '"pubkey"'; then
    PUBKEY=$(echo "$PUBKEY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pubkey","")[:60])')
    echo "  teacher panel pubkey: ${PUBKEY}..."
    echo "  (bootstrap-students.sh will fetch this to push to each student)"
else
    echo "  WARN: /api/teacher/pubkey didn't return a pubkey:"
    echo "  $PUBKEY_JSON"
    echo "  Check 'docker logs otlab-teacher' — pubkey is generated on first start"
fi

echo
echo "── 5. Strip NOPASSWD sudo from imager user ($IMAGER_USER) ──────"
SUDOERS_FILE=/etc/sudoers.d/099_${IMAGER_USER}_nopasswd
if [ -f "$SUDOERS_FILE" ]; then
    sudo rm "$SUDOERS_FILE"
    echo "  removed $SUDOERS_FILE"
    echo "  $IMAGER_USER still exists but no longer has passwordless sudo"
    echo "  (otadmin remains the canonical NOPASSWD sudo user)"
else
    echo "  $SUDOERS_FILE not present — already clean"
fi
# Also check if the imager user is in the sudo group (some Pi Imager setups do this)
if id -nG "$IMAGER_USER" 2>/dev/null | tr ' ' '\n' | grep -qx sudo; then
    sudo deluser "$IMAGER_USER" sudo 2>&1 | tail -1
    echo "  removed $IMAGER_USER from sudo group"
fi

echo
echo "── 6. Stamp /etc/otlab-bootstrap-info ──────────────────────────"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
sudo tee -a /etc/otlab-bootstrap-info >/dev/null <<STAMP
finalize_ts=$TS
finalize_script=finalize-teacher-pi.sh
STAMP
echo "  appended finalize timestamp"

echo
echo "==> finalize complete"
WORKER_EOF

scp -q "$WORKER" "$PI_HOST:/tmp/finalize-worker.sh"
rm -f "$WORKER"
ssh "$PI_HOST" "chmod +x /tmp/finalize-worker.sh && bash /tmp/finalize-worker.sh $IMAGER_USER && rm /tmp/finalize-worker.sh"

# ── post-script reminders for things requiring interactive input ────
cat <<EOF

==> THINGS YOU NEED TO DO MANUALLY (interactive):

  1) Enroll Tailscale on the teacher Pi:
       ssh $PI_HOST 'sudo tailscale up --hostname=otlab-teacher --ssh'
     (will print a URL; open on your laptop to OAuth approve)

  2) Rotate event-day passwords when classroom rollout is imminent:
       - otadmin SSH password (only used in emergency; key auth is primary)
       - Grafana admin password:  visit http://<pi>:3000 -> Configuration -> Users -> admin
       - Teacher panel basic-auth password: re-run install-teacher-panel.sh
         with DASH_PASS='<new-strong-pw>' env

  3) Verify Tailscale is happy:
       ssh $PI_HOST 'tailscale status'
       ssh $PI_HOST 'tailscale ip -4'
EOF
