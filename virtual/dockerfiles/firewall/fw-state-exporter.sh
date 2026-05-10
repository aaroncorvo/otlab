#!/bin/sh
# fw-state-exporter.sh — long-running background loop inside the
# firewall container. Every 5 seconds, snapshots iptables rules
# (with packet/byte counters) + conntrack summary into the shared
# state directory. The dashboard container mounts that directory
# read-only and reads these files to populate the Firewall tab.
#
# Why a separate script (not inlined in start-firewall.sh):
# Alpine's ash doesn't reliably support `exec -a NAME` for
# named-process backgrounding, and clab's exec-hook context can
# reap subshell jobs. A standalone script invoked via setsid is
# clean: it gets its own PGID, survives the exec hook returning,
# and stays observable as a single named process for pgrep.
#
# Output files (atomic rename, never partial reads):
#   $FW_STATE_DIR/iptables.json   - all chains + NAT, rendered as JSON
#   $FW_STATE_DIR/conntrack.txt   - conntrack -C count + tail of -L

set -u

FW_STATE_DIR=${FW_STATE_DIR:-/var/lib/otlab/fw-state}
INTERVAL=${FW_STATE_EXPORT_INTERVAL:-5}

mkdir -p "$FW_STATE_DIR"

# JSON-escape: backslashes first, then double-quotes; newlines stay as
# real newlines and are converted to literal \n in the awk pass below.
escape_json() {
    sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

dump_chain_json() {
    table=$1
    chain=$2
    if [ "$table" = "filter" ]; then
        body=$(iptables -nvL "$chain" --line-numbers 2>&1 | escape_json)
    else
        body=$(iptables -t "$table" -nvL "$chain" --line-numbers 2>&1 | escape_json)
    fi
    # Convert literal newlines -> \n so the result is one valid JSON string
    body=$(printf '%s' "$body" | awk 'BEGIN{ORS=""} {if(NR>1)printf "\\n"; printf "%s",$0}')
    printf '"%s":"%s"' "$chain" "$body"
}

dump_iptables_json() {
    ts=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)
    tmp="$FW_STATE_DIR/.iptables.json.tmp"

    {
        printf '{"ts":"%s","chains":{' "$ts"
        first=1
        for chain in INPUT FORWARD OUTPUT; do
            [ $first -eq 0 ] && printf ','
            dump_chain_json filter "$chain"
            first=0
        done
        printf '},"nat":{'
        first=1
        for chain in PREROUTING POSTROUTING; do
            [ $first -eq 0 ] && printf ','
            dump_chain_json nat "$chain"
            first=0
        done
        printf '}}'
    } >"$tmp" 2>/dev/null

    mv -f "$tmp" "$FW_STATE_DIR/iptables.json" 2>/dev/null
}

dump_conntrack() {
    ts=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)
    tmp="$FW_STATE_DIR/.conntrack.txt.tmp"
    {
        echo "# $ts"
        echo "## conntrack count"
        conntrack -C 2>/dev/null || echo "(conntrack unavailable in this kernel)"
        echo
        echo "## conntrack flows (last 50)"
        conntrack -L 2>/dev/null | tail -50
    } >"$tmp" 2>/dev/null
    mv -f "$tmp" "$FW_STATE_DIR/conntrack.txt" 2>/dev/null
}

echo "==> fw-state-exporter starting (interval ${INTERVAL}s, dir $FW_STATE_DIR)"
while true; do
    dump_iptables_json
    dump_conntrack
    sleep "$INTERVAL"
done
