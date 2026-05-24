#!/usr/bin/env bash
# otlab-reset.sh — wipe a student Pi back to a known clean state.
#
# Two modes:
#   --step   between-lab-step reset (keep teacher-pushed keys, fabric stays up
#            but PLC + sensor + IDS state wiped). ~30s.
#   --full   end-of-class reset (everything --step does + redeploys clab from
#            scratch + clears teacher's authorized_keys + wipes dashboard
#            SQLite). ~3 min. Pi is left in a "freshly installed" state — no
#            re-image needed.
#
# Keeps in both modes:
#   /etc/otlab/student.env       (the Pi is still student #N)
#   Pi OS itself                 (no re-image needed)
#   otadmin / otuser accounts    (bootstrap-users.sh state is preserved)
#
# Usage:
#   ./scripts/otlab-reset.sh --step otadmin@otlab-student-05.local
#   ./scripts/otlab-reset.sh --full otadmin@otlab-student-05.local
#
# Flags:
#   --dry-run      Print what would happen, don't execute
#   --yes          Skip confirm prompt
#
# Idempotent — safe to re-run.

set -euo pipefail

MODE=""
PI_HOST=""
DRY_RUN="no"
ASSUME_YES="no"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --step)     MODE="step"; shift ;;
        --full)     MODE="full"; shift ;;
        --dry-run)  DRY_RUN="yes"; shift ;;
        --yes|-y)   ASSUME_YES="yes"; shift ;;
        --help|-h)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)          PI_HOST="$1"; shift ;;
    esac
done

if [[ -z "$MODE" || -z "$PI_HOST" ]]; then
    echo "Usage: $0 --step|--full otadmin@<pi>.local" >&2
    exit 1
fi

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mXX \033[0m %s\n' "$*" >&2; exit 1; }

ssh_run() {
    if [[ "$DRY_RUN" == "yes" ]]; then
        echo "    [dry-run] ssh $PI_HOST '$*'"
    else
        ssh "$PI_HOST" "$@"
    fi
}

# ── identify the Pi ───────────────────────────────────────────────────
say "OTLab reset — $MODE mode"
echo "    Target: $PI_HOST"
echo

ENV_CONTENT="$(ssh "$PI_HOST" 'cat /etc/otlab/student.env 2>/dev/null || true')"
if [[ -z "$ENV_CONTENT" ]]; then
    die "/etc/otlab/student.env missing on $PI_HOST — run otlab-install.sh first."
fi

STUDENT_ID="$(echo "$ENV_CONTENT" | grep -E '^STUDENT_ID=' | cut -d= -f2 || true)"
ROLE="$(echo "$ENV_CONTENT" | grep -E '^ROLE=' | cut -d= -f2 || true)"
HOSTNAME="$(echo "$ENV_CONTENT" | grep -E '^STUDENT_HOSTNAME=' | cut -d= -f2 || true)"

echo "    Role:       $ROLE"
echo "    Hostname:   $HOSTNAME"
[[ -n "$STUDENT_ID" ]] && echo "    Student #:  $STUDENT_ID"
echo

if [[ "$ROLE" != "student" ]]; then
    die "Reset is for student Pis only. This Pi has role=$ROLE."
fi

# ── confirm ───────────────────────────────────────────────────────────
if [[ "$MODE" == "full" ]]; then
    warn "FULL reset will wipe:"
    echo "    - clab fabric (destroy + redeploy)"
    echo "    - dashboard SQLite (audit log, settings)"
    echo "    - teacher's authorized_keys (Pi will need re-bootstrap from teacher)"
    echo "    - /var/log/* (all log buffers)"
    echo "    Preserved: /etc/otlab/student.env, otadmin/otuser, Pi OS itself"
else
    say "STEP reset will wipe:"
    echo "    - PLC program state (re-loads canonical .st)"
    echo "    - sensor-sim history (baseline scenario)"
    echo "    - iptables counters + conntrack table"
    echo "    - Suricata EVE JSON buffer"
    echo "    - dashboard captures dir"
    echo "    Preserved: teacher keys, clab fabric, /etc/otlab/student.env"
fi
echo

if [[ "$ASSUME_YES" != "yes" ]]; then
    read -r -p "Proceed with $MODE reset? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || die "aborted by user"
fi

# ── --step actions (both modes do these) ──────────────────────────────
say "[step] stopping PLC + sensor-sim services"
ssh_run "sudo systemctl stop sensor-sim.service dnp3-outstation.service 2>/dev/null || true"

say "[step] resetting OpenPLC runtime (reloading canonical .st)"
ssh_run "sudo systemctl stop openplc.service 2>/dev/null || true"
ssh_run "rm -f /home/otuser/lab/runtime/*.so /home/otuser/lab/runtime/*.log 2>/dev/null || true"

say "[step] clearing sensor-sim history"
ssh_run "sudo rm -rf /var/lib/otlab/sensor-sim/*.json 2>/dev/null || true"

say "[step] flushing iptables counters + conntrack"
ssh_run "sudo iptables -Z 2>/dev/null || true; sudo conntrack -F 2>/dev/null || true"

say "[step] truncating Suricata EVE JSON"
ssh_run "sudo truncate -s 0 /var/log/suricata/eve.json 2>/dev/null || true"

say "[step] clearing dashboard captures"
ssh_run "sudo rm -rf /var/lib/otlab/dashboard/captures/* 2>/dev/null || true"

say "[step] restarting services"
ssh_run "sudo systemctl start sensor-sim.service dnp3-outstation.service openplc.service 2>/dev/null || true"

# ── --full additional actions ─────────────────────────────────────────
if [[ "$MODE" == "full" ]]; then
    say "[full] destroying + redeploying clab fabric"
    ssh_run "cd /home/otuser/lab/virtual && sudo containerlab destroy -t topologies/otlab.clab.yaml 2>/dev/null || true"
    # Re-render from template in case student.env or the .tmpl changed
    # since last install. Falls back to single-Pi defaults if student.env
    # is missing.
    ssh_run "test -x /home/otuser/lab/scripts/render-topology.sh && bash /home/otuser/lab/scripts/render-topology.sh || echo '    (render script missing — using existing yaml)'"
    ssh_run "cd /home/otuser/lab/virtual && sudo containerlab deploy  -t topologies/otlab.clab.yaml"

    say "[full] wiping dashboard SQLite (audit log, settings)"
    ssh_run "sudo rm -f /var/lib/otlab/dashboard/*.db 2>/dev/null || true"
    ssh_run "sudo systemctl restart otlab-dashboard.service 2>/dev/null || true"

    say "[full] revoking teacher's authorized_keys"
    ssh_run "rm -f /home/otadmin/.ssh/authorized_keys.bak 2>/dev/null; \
             mv /home/otadmin/.ssh/authorized_keys /home/otadmin/.ssh/authorized_keys.bak 2>/dev/null || true; \
             touch /home/otadmin/.ssh/authorized_keys; \
             chmod 0600 /home/otadmin/.ssh/authorized_keys"
    ssh_run "sudo rm -f /etc/ssh/sshd_config.d/99-teacher-key-only.conf 2>/dev/null || true"
    ssh_run "sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd 2>/dev/null || true"

    say "[full] clearing /var/log/* (buffers, not journal)"
    ssh_run "sudo find /var/log -type f \( -name '*.log' -o -name '*.gz' \) -exec truncate -s 0 {} \; 2>/dev/null || true"
    ssh_run "sudo truncate -s 0 /var/log/journal/*/*.journal 2>/dev/null || sudo journalctl --rotate && sudo journalctl --vacuum-size=10M"

    say "[full] resetting promtail position file (re-ship from current tail)"
    ssh_run "sudo rm -f /var/lib/promtail/positions.yaml 2>/dev/null; sudo systemctl restart promtail-otlab.service 2>/dev/null || true"
fi

echo
say "reset complete ($MODE mode)"
if [[ "$MODE" == "full" ]]; then
    echo "    The Pi is back to a fresh-install state."
    echo "    To re-arm for class: from the teacher host, run"
    echo "        ./teacher/bootstrap-students.sh $PI_HOST"
    echo "    to push the teacher's pubkey + re-lock password auth."
else
    echo "    PLC + sensor + IDS state wiped. Teacher panel still reaches this Pi."
    echo "    Students can start the next lab step immediately."
fi
