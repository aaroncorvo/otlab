#!/usr/bin/env bash
# test-ssh-bruteforce.sh — controlled SSH-failure generator. Fires N
# failed-auth attempts at a target Pi to populate the dashboard's
# failed_ssh_1h counter and demonstrate detection.
#
# Use this BEFORE installing fail2ban (to baseline) and AFTER (to verify
# auto-ban actually works).

set -e
TARGET="${1:-10.20.30.47}"
COUNT="${2:-10}"
USER_FAKE="hacker$(date +%s)"

echo "=== firing ${COUNT} failed SSH attempts at ${TARGET} as fake user '${USER_FAKE}' ==="
echo "(SSH will time out on each attempt — uses BatchMode + a non-existent key)"
echo

for i in $(seq 1 $COUNT); do
    ssh -o BatchMode=yes \
        -o PreferredAuthentications=publickey \
        -o IdentityFile=/dev/null \
        -o ConnectTimeout=3 \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR \
        "${USER_FAKE}@${TARGET}" "true" 2>&1 || true
    printf "  attempt %2d: failed (expected)\n" "$i"
    sleep 0.5
done

echo
echo "=== verification ==="
echo "Wait ~10 seconds for the dashboard probe loop to refresh, then:"
echo "  - Live Data tab → System Health card for ${TARGET}'s host"
echo "  - 'SSH fails 1h' counter should be ≥ ${COUNT}"
echo
echo "On the target Pi, journalctl shows the failures:"
echo "  ssh otadmin@${TARGET} 'sudo journalctl -u ssh --since \"5 min ago\" | grep -c \"Invalid user\"'"
echo
echo "If fail2ban is installed, after ~5 attempts your IP gets banned:"
echo "  ssh otadmin@${TARGET} 'sudo fail2ban-client status sshd'"
echo
echo "Cleanup (if banned):"
echo "  ssh otadmin@${TARGET} 'sudo fail2ban-client unban --all'  # via console / out-of-band"
