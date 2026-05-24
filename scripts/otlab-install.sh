#!/usr/bin/env bash
# otlab-install.sh — interactive classroom installer.
#
# Asks "what role is this Pi?" → teacher or student.
# If student: asks "which student # (1-20)?" → bakes per-student IPs.
# Then runs the right bootstrap chain.
#
# Writes /etc/otlab/student.env on the Pi as the canonical per-Pi config.
# All downstream scripts (containerlab render, dashboard, promtail) read
# from it.
#
# Usage:
#   ./scripts/otlab-install.sh otadmin@<pi>.local
#
# Flags:
#   --role teacher|student         Skip role prompt
#   --student-id N                 Skip student-N prompt (1-20)
#   --reinstall                    Overwrite existing /etc/otlab/student.env
#   --dry-run                      Print what would happen, don't execute
#   --yes                          Skip confirm prompt
#
# Env (all have defaults — override per-cohort):
#   CLASSROOM_SEGMENT  192.168.10.0/24    base classroom subnet
#   CLASSROOM_GATEWAY  192.168.10.1       upstream router (MikroTik/FortiGate)
#   TEACHER_IP         192.168.10.10      teacher panel + SIEM host
#   STUDENT_IP_BASE    192.168.10.100     student #N gets STUDENT_IP_BASE+N
#   DMZ_OCTETS         10.75              full subnet: ${DMZ_OCTETS}.${N}.0/24
#   PCN_OCTETS         10.30
#   ENT_OCTETS         10.50
#   MAX_STUDENTS       20
#   SIEM_LOKI_PORT     3100
#   LAB_VERSION        v4.0

set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────────
CLASSROOM_SEGMENT="${CLASSROOM_SEGMENT:-192.168.10.0/24}"
CLASSROOM_GATEWAY="${CLASSROOM_GATEWAY:-192.168.10.1}"
TEACHER_IP="${TEACHER_IP:-192.168.10.10}"
STUDENT_IP_BASE="${STUDENT_IP_BASE:-192.168.10.100}"
DMZ_OCTETS="${DMZ_OCTETS:-10.75}"
PCN_OCTETS="${PCN_OCTETS:-10.30}"
ENT_OCTETS="${ENT_OCTETS:-10.50}"
MAX_STUDENTS="${MAX_STUDENTS:-20}"
SIEM_LOKI_PORT="${SIEM_LOKI_PORT:-3100}"
LAB_VERSION="${LAB_VERSION:-v4.0}"

ROLE=""
STUDENT_ID=""
REINSTALL="no"
DRY_RUN="no"
ASSUME_YES="no"

# ── arg parsing ────────────────────────────────────────────────────────
PI_HOST=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --role)        ROLE="$2"; shift 2 ;;
        --student-id)  STUDENT_ID="$2"; shift 2 ;;
        --reinstall)   REINSTALL="yes"; shift ;;
        --dry-run)     DRY_RUN="yes"; shift ;;
        --yes|-y)      ASSUME_YES="yes"; shift ;;
        --help|-h)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)             PI_HOST="$1"; shift ;;
    esac
done

if [[ -z "$PI_HOST" ]]; then
    echo "Usage: $0 [--role teacher|student] [--student-id N] otadmin@<pi>.local" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"

# ── helpers ───────────────────────────────────────────────────────────
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mXX \033[0m %s\n' "$*" >&2; exit 1; }

run() {
    if [[ "$DRY_RUN" == "yes" ]]; then
        echo "    [dry-run] $*"
    else
        eval "$@"
    fi
}

ssh_run() {
    if [[ "$DRY_RUN" == "yes" ]]; then
        echo "    [dry-run-ssh] $*"
    else
        ssh "$PI_HOST" "$@"
    fi
}

# ── reachability check ────────────────────────────────────────────────
say "OTLab classroom installer"
echo "    Target:  $PI_HOST"
echo "    Repo:    $REPO_ROOT"
echo "    Mode:    $([ "$DRY_RUN" = "yes" ] && echo "DRY RUN" || echo "live")"
echo

say "checking reachability"
if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$PI_HOST" 'sudo -n true 2>/dev/null'; then
    die "Can't SSH to $PI_HOST with passwordless sudo. Run bootstrap-users.sh first."
fi
echo "    reachable + sudo ok"
echo

# ── check for existing install ────────────────────────────────────────
EXISTING_ENV="$(ssh "$PI_HOST" 'cat /etc/otlab/student.env 2>/dev/null || true')"
if [[ -n "$EXISTING_ENV" ]] && [[ "$REINSTALL" != "yes" ]]; then
    warn "/etc/otlab/student.env already exists on $PI_HOST:"
    echo "$EXISTING_ENV" | sed 's/^/      /'
    echo
    echo "    Re-run with --reinstall to overwrite, or run otlab-reset.sh --full first."
    exit 1
fi

# ── role prompt ───────────────────────────────────────────────────────
if [[ -z "$ROLE" ]]; then
    echo "What role is this Pi?"
    echo "  1) teacher  — runs teacher panel + Loki/Grafana SIEM"
    echo "  2) student  — runs OTLab fabric + ships logs to teacher SIEM"
    read -r -p "> " choice
    case "$choice" in
        1|teacher) ROLE="teacher" ;;
        2|student) ROLE="student" ;;
        *) die "Invalid choice: $choice" ;;
    esac
fi

[[ "$ROLE" == "teacher" || "$ROLE" == "student" ]] || die "ROLE must be teacher or student (got: $ROLE)"

# ── student-id prompt ────────────────────────────────────────────────
if [[ "$ROLE" == "student" ]]; then
    if [[ -z "$STUDENT_ID" ]]; then
        echo
        echo "Which student number? (1-$MAX_STUDENTS)"
        read -r -p "> " STUDENT_ID
    fi
    if ! [[ "$STUDENT_ID" =~ ^[0-9]+$ ]] || [[ "$STUDENT_ID" -lt 1 ]] || [[ "$STUDENT_ID" -gt "$MAX_STUDENTS" ]]; then
        die "STUDENT_ID must be an integer 1-$MAX_STUDENTS (got: $STUDENT_ID)"
    fi
fi

# ── derive per-Pi values ──────────────────────────────────────────────
if [[ "$ROLE" == "teacher" ]]; then
    HOSTNAME_NEW="otlab-teacher"
    CLASSROOM_IP="$TEACHER_IP"
    DMZ_NET=""
    PCN_NET=""
    ENT_NET=""
else
    NN=$(printf '%02d' "$STUDENT_ID")
    HOSTNAME_NEW="otlab-student-$NN"
    # STUDENT_IP_BASE=192.168.10.100, student #5 → .105
    base_octet="${STUDENT_IP_BASE##*.}"
    base_prefix="${STUDENT_IP_BASE%.*}"
    CLASSROOM_IP="${base_prefix}.$((base_octet + STUDENT_ID))"
    DMZ_NET="${DMZ_OCTETS}.${STUDENT_ID}"
    PCN_NET="${PCN_OCTETS}.${STUDENT_ID}"
    ENT_NET="${ENT_OCTETS}.${STUDENT_ID}"
fi

# ── confirm ───────────────────────────────────────────────────────────
echo
say "summary"
echo "  Role:           $ROLE"
echo "  Hostname:       $HOSTNAME_NEW"
echo "  Classroom IP:   $CLASSROOM_IP  (must match router DHCP reservation)"
if [[ "$ROLE" == "student" ]]; then
    echo "  Student ID:     $STUDENT_ID"
    echo "  DMZ fabric:     ${DMZ_NET}.0/24"
    echo "  PCN fabric:     ${PCN_NET}.0/24"
    echo "  ENT fabric:     ${ENT_NET}.0/24  (V4.1 — not deployed)"
    echo "  SIEM target:    http://${TEACHER_IP}:${SIEM_LOKI_PORT}"
else
    echo "  Teacher panel:  http://${TEACHER_IP}:8080"
    echo "  SIEM Grafana:   http://${TEACHER_IP}:3000"
fi
echo

if [[ "$ASSUME_YES" != "yes" ]]; then
    read -r -p "Proceed? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || die "aborted by user"
fi

# ── write /etc/otlab/student.env ──────────────────────────────────────
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
say "writing /etc/otlab/student.env"
ENV_CONTENT=$(cat <<EOF
# /etc/otlab/student.env — written by scripts/otlab-install.sh
ROLE=$ROLE
$([ "$ROLE" = "student" ] && echo "STUDENT_ID=$STUDENT_ID")
STUDENT_HOSTNAME=$HOSTNAME_NEW
CLASSROOM_SEGMENT=$CLASSROOM_SEGMENT
CLASSROOM_GATEWAY=$CLASSROOM_GATEWAY
CLASSROOM_IP=$CLASSROOM_IP
TEACHER_IP=$TEACHER_IP
$([ "$ROLE" = "student" ] && echo "DMZ_NET=$DMZ_NET")
$([ "$ROLE" = "student" ] && echo "PCN_NET=$PCN_NET")
$([ "$ROLE" = "student" ] && echo "ENT_NET=$ENT_NET")
SIEM_LOKI_URL=http://${TEACHER_IP}:${SIEM_LOKI_PORT}
SIEM_PROMTAIL_PORT=9080
LAB_VERSION=$LAB_VERSION
INSTALLED_AT=$TS
INSTALLED_BY=otlab-install.sh
EOF
)

if [[ "$DRY_RUN" != "yes" ]]; then
    ssh "$PI_HOST" "sudo mkdir -p /etc/otlab && echo '$ENV_CONTENT' | sudo tee /etc/otlab/student.env >/dev/null && sudo chmod 644 /etc/otlab/student.env"
else
    echo "$ENV_CONTENT" | sed 's/^/    /'
fi

# ── set hostname ──────────────────────────────────────────────────────
say "setting hostname → $HOSTNAME_NEW"
ssh_run "sudo hostnamectl set-hostname $HOSTNAME_NEW && sudo sed -i.bak 's/^127.0.1.1.*/127.0.1.1\\t$HOSTNAME_NEW/' /etc/hosts"

# ── run the bootstrap chain ───────────────────────────────────────────
echo
if [[ "$ROLE" == "student" ]]; then
    say "running student bootstrap chain"
    echo "    [1/5] bootstrap-pi.sh                ~15 min (OpenPLC compile)"
    run "$SCRIPTS_DIR/bootstrap-pi.sh \"$PI_HOST\""
    echo "    [2/5] bootstrap-l3-mon-role.sh       ~5 min  (Docker + Suricata)"
    run "$SCRIPTS_DIR/bootstrap-l3-mon-role.sh \"$PI_HOST\""
    # Pi with 4+ NICs (Cruiser Keel etc.) — pin the role→MAC map.
    # No-op on Pis with <4 NICs.
    echo "    [3/5] configure-4port-pi.sh          ~30s    (Cruiser Keel port naming, no-op if <4 NICs)"
    if [[ -x "$SCRIPTS_DIR/configure-4port-pi.sh" ]]; then
        run "$SCRIPTS_DIR/configure-4port-pi.sh \"$PI_HOST\""
    fi
    echo "    [4/5] install-virtual-lab.sh         ~10 min (build 7 OTLab images)"
    run "$SCRIPTS_DIR/install-virtual-lab.sh \"$PI_HOST\""
    echo "    [5/5] install-student-promtail.sh    ~1 min  (log shipper → teacher SIEM)"
    if [[ -x "$REPO_ROOT/teacher/agents/install-student-promtail.sh" ]]; then
        run "$REPO_ROOT/teacher/agents/install-student-promtail.sh \"$PI_HOST\""
    else
        warn "teacher/agents/install-student-promtail.sh not found — skipping log shipper install"
    fi
else
    say "running teacher bootstrap chain"
    echo "    [1/3] bootstrap-pi.sh                ~10 min (apt + Docker)"
    run "$SCRIPTS_DIR/bootstrap-pi.sh \"$PI_HOST\""
    echo "    [2/3] deploy teacher panel container"
    run "$SCRIPTS_DIR/install-teacher-panel.sh \"$PI_HOST\" 2>/dev/null || echo '    (install-teacher-panel.sh not present — see teacher/README.md for manual docker run)'"
    echo "    [3/3] deploy SIEM stack (Loki + Grafana + Promtail)"
    run "$SCRIPTS_DIR/install-siem.sh \"$PI_HOST\" 2>/dev/null || echo '    (install-siem.sh not present — see teacher/siem/README.md for manual deploy)'"
fi

echo
say "install complete"
if [[ "$ROLE" == "student" ]]; then
    echo "    Pi will appear in the teacher panel within ~30s"
    echo "    Suricata + dashboard logs will appear in Grafana within ~1 min"
    echo "    After all student Pis are installed, run from the teacher host:"
    echo "        ./teacher/bootstrap-students.sh --range ${STUDENT_IP_BASE%.*}.$((${STUDENT_IP_BASE##*.}+1))-$((${STUDENT_IP_BASE##*.}+MAX_STUDENTS))"
    echo "    to push the teacher's SSH pubkey + disable password auth on all students."
else
    echo "    Open the teacher panel:  http://${TEACHER_IP}:8080  (otlab / P@ssw0rd!)"
    echo "    Open Grafana SIEM:       http://${TEACHER_IP}:3000  (admin / P@ssw0rd!)"
    echo "    Next: run otlab-install.sh against each student Pi (1-${MAX_STUDENTS})."
fi
