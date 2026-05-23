#!/usr/bin/env bash
# bootstrap-students.sh — push the teacher panel's pubkey to a set of
# student Pis, then disable password SSH on each so the teacher's key
# becomes the only credential that opens them.
#
# Run on the host where the teacher panel container is running (it needs
# HTTP reach to the panel's port + SSH reach to each student).
#
# Usage:
#   ./teacher/bootstrap-students.sh 192.168.1.101 192.168.1.102 192.168.1.103
#
# Or scan a range:
#   ./teacher/bootstrap-students.sh --range 192.168.1.100-120
#
# Env (all have defaults):
#   TEACHER_URL    URL of the running teacher panel  (default http://localhost:8080)
#   DASH_USER      Panel basic-auth user             (default otlab)
#   DASH_PASS      Panel basic-auth pass             (default P@ssw0rd!)
#   SSH_USER       Student SSH username              (default otadmin)
#   SSH_PASS       Student SSH password — used ONCE
#                  during bootstrap, then disabled    (default P@ssw0rd!)
#   DISABLE_PW     Disable PasswordAuthentication after key push? (default yes)
#   DRY_RUN        Print actions without executing   (default no)
#
# After this runs each student Pi has:
#   - Teacher pubkey in /home/otadmin/.ssh/authorized_keys
#   - /etc/ssh/sshd_config.d/99-teacher-key-only.conf forcing key-only auth
#   - sshd restarted
# And: no SSH keys of its own, no Tailscale auth, no outbound creds.

set -euo pipefail

TEACHER_URL="${TEACHER_URL:-http://localhost:8080}"
DASH_USER="${DASH_USER:-otlab}"
DASH_PASS="${DASH_PASS:-P@ssw0rd!}"
SSH_USER="${SSH_USER:-otadmin}"
SSH_PASS="${SSH_PASS:-P@ssw0rd!}"
DISABLE_PW="${DISABLE_PW:-yes}"
DRY_RUN="${DRY_RUN:-no}"

# ── arg parsing ────────────────────────────────────────────────────────
TARGETS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --range)
            # Accept either "192.168.1.100-120" or two args "192.168.1.100" "192.168.1.120"
            shift
            range="$1"; shift
            base="${range%.*}"; rng="${range##*.}"
            start="${rng%-*}"; end="${rng##*-}"
            for ((i = start; i <= end; i++)); do TARGETS+=("$base.$i"); done
            ;;
        --help|-h)
            sed -n '1,/^set -euo pipefail/p' "$0" | sed '$d'
            exit 0
            ;;
        *)
            TARGETS+=("$1"); shift
            ;;
    esac
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    echo "Usage: $0 <student-ip> [<student-ip>...]" >&2
    echo "       $0 --range 192.168.1.100-120" >&2
    exit 1
fi

# ── deps ───────────────────────────────────────────────────────────────
if ! command -v sshpass >/dev/null 2>&1; then
    cat >&2 <<EOF
ERROR: sshpass not installed (needed for one-time password SSH during bootstrap).
  macOS:  brew install hudochenkov/sshpass/sshpass
  Debian: sudo apt install sshpass
EOF
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl not installed." >&2
    exit 1
fi

# ── fetch the teacher's pubkey from the panel ──────────────────────────
echo "==> fetching pubkey from $TEACHER_URL"
PUBKEY_JSON=$(curl -fsS -u "$DASH_USER:$DASH_PASS" "$TEACHER_URL/api/teacher/pubkey" || true)
if [[ -z "$PUBKEY_JSON" ]]; then
    echo "ERROR: couldn't reach $TEACHER_URL/api/teacher/pubkey" >&2
    echo "       Is the teacher panel running? Did you set TEACHER_URL?" >&2
    exit 1
fi

PUBKEY=$(printf '%s' "$PUBKEY_JSON" | python3 -c \
    "import json, sys; d = json.load(sys.stdin); print(d.get('pubkey', ''))")

if [[ -z "$PUBKEY" ]]; then
    echo "ERROR: panel didn't return a pubkey. Is SSH_KEY_PATH set on the panel?" >&2
    echo "       Response: $PUBKEY_JSON" >&2
    exit 1
fi

echo "    pubkey: ${PUBKEY:0:40}…"
echo "    targets: ${#TARGETS[@]} hosts"
[[ "$DISABLE_PW" == "yes" ]] && echo "    will disable PasswordAuthentication after key push"
[[ "$DRY_RUN"   == "yes" ]] && echo "    DRY RUN — not making any changes"
echo

# ── push to each student ───────────────────────────────────────────────
FAILED=()

for IP in "${TARGETS[@]}"; do
    echo "==> $IP"

    if [[ "$DRY_RUN" == "yes" ]]; then
        echo "    (dry run — would push pubkey + disable password auth)"
        continue
    fi

    # The remote script: lay down authorized_keys + optionally drop a
    # config snippet that disables password auth and reload sshd.
    remote=$(cat <<REMOTE_EOF
set -e
mkdir -p ~/.ssh
chmod 0700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 0600 ~/.ssh/authorized_keys
if ! grep -qF '$PUBKEY' ~/.ssh/authorized_keys 2>/dev/null; then
    echo '$PUBKEY' >> ~/.ssh/authorized_keys
    echo '    pubkey appended to authorized_keys'
else
    echo '    pubkey already present (idempotent)'
fi
REMOTE_EOF
)

    if [[ "$DISABLE_PW" == "yes" ]]; then
        remote+=$(cat <<REMOTE_EOF

sudo tee /etc/ssh/sshd_config.d/99-teacher-key-only.conf >/dev/null <<CFG
# Written by teacher/bootstrap-students.sh — teacher key is the only credential
PasswordAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
CFG
sudo systemctl reload ssh || sudo systemctl reload sshd
echo '    PasswordAuthentication disabled, sshd reloaded'
REMOTE_EOF
)
    fi

    # Use sshpass for the one-time password-based SSH (only path that
    # works pre-bootstrap). After this completes, password auth is gone
    # and the next connection MUST use the teacher's key.
    if sshpass -p "$SSH_PASS" ssh \
            -o StrictHostKeyChecking=accept-new \
            -o ConnectTimeout=5 \
            -o LogLevel=ERROR \
            "$SSH_USER@$IP" "$remote"
    then
        echo "    ok"
    else
        echo "    FAILED" >&2
        FAILED+=("$IP")
    fi
done

# ── summary ────────────────────────────────────────────────────────────
echo
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "==> all ${#TARGETS[@]} hosts bootstrapped successfully"
    echo "    The teacher panel will now reach them via key auth."
    [[ "$DISABLE_PW" == "yes" ]] && \
        echo "    Password auth is disabled — the only way in is the teacher's private key."
else
    echo "==> ${#FAILED[@]} hosts FAILED:"
    printf '    %s\n' "${FAILED[@]}"
    echo
    echo "    Most common cause: wrong SSH_PASS, or sshd not yet running on the Pi."
    echo "    Try one host manually: sshpass -p '$SSH_PASS' ssh $SSH_USER@<ip> echo ok"
    exit 1
fi
