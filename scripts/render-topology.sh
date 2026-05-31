#!/usr/bin/env bash
# render-topology.sh — render virtual/topologies/otlab.clab.yaml from
# its .tmpl, substituting __DMZ_NET__ / __PCN_NET__ with per-student
# values from /etc/otlab/student.env (or env vars).
#
# Runs on the Pi (after otlab-install.sh has written student.env).
# install-virtual-lab.sh calls this before `containerlab deploy`.
#
# Usage:
#   ./scripts/render-topology.sh                  # in-place: tmpl → yaml
#   ./scripts/render-topology.sh --check          # validate placeholders + render to /tmp; don't overwrite
#   ./scripts/render-topology.sh --out PATH       # render to a different path
#   ./scripts/render-topology.sh --env-file PATH  # use a different env file
#
# Env (overrides /etc/otlab/student.env values if both set):
#   DMZ_NET    first-3-octets of DMZ subnet  (default: 192.168.75 = single-Pi)
#   PCN_NET    first-3-octets of PCN subnet  (default: 10.20.30   = single-Pi)
#
# If /etc/otlab/student.env doesn't exist AND no env vars are set, falls
# back to the single-Pi defaults (192.168.75 / 10.20.30) — backward
# compatible with all single-Pi flows.

set -euo pipefail

CHECK="no"
ENV_FILE="${ENV_FILE:-/etc/otlab/student.env}"
TMPL_DEFAULT="virtual/topologies/otlab.clab.yaml.tmpl"
OUT_DEFAULT="virtual/topologies/otlab.clab.yaml"
TMPL=""
OUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)     CHECK="yes"; shift ;;
        --env-file)  ENV_FILE="$2"; shift 2 ;;
        --tmpl)      TMPL="$2"; shift 2 ;;
        --out)       OUT="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

# ── resolve paths relative to repo root if not absolute ──────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Default tmpl/out — try repo layout first, then current dir (when this
# script is rsync'd to the Pi alongside the topology files)
if [[ -z "$TMPL" ]]; then
    if   [[ -f "$REPO_ROOT/$TMPL_DEFAULT" ]]; then TMPL="$REPO_ROOT/$TMPL_DEFAULT"
    elif [[ -f "$TMPL_DEFAULT" ]];               then TMPL="$TMPL_DEFAULT"
    elif [[ -f "$(pwd)/$TMPL_DEFAULT" ]];        then TMPL="$(pwd)/$TMPL_DEFAULT"
    else echo "ERROR: can't find $TMPL_DEFAULT (pass --tmpl)" >&2; exit 1
    fi
fi

if [[ -z "$OUT" ]]; then
    OUT="$(dirname "$TMPL")/$(basename "$TMPL" .tmpl)"
fi

if [[ "$CHECK" == "yes" ]]; then
    # Portable mktemp (works on BSD/macOS + GNU/Linux)
    OUT="$(mktemp -t otlab-render-XXXXXX).yaml"
fi

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# ── load student.env if it exists ────────────────────────────────────
DMZ_NET="${DMZ_NET:-}"
PCN_NET="${PCN_NET:-}"
ROLE="${ROLE:-}"
STUDENT_ID="${STUDENT_ID:-}"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

# ── apply defaults (single-Pi historical addressing) ─────────────────
DMZ_NET="${DMZ_NET:-192.168.75}"
PCN_NET="${PCN_NET:-10.20.30}"

# ── render ───────────────────────────────────────────────────────────
say "rendering topology"
echo "    tmpl:    $TMPL"
echo "    out:     $OUT"
echo "    DMZ_NET: $DMZ_NET"
echo "    PCN_NET: $PCN_NET"
[[ -n "$STUDENT_ID" ]] && echo "    student: #$STUDENT_ID ($ROLE)"

# Pi hostname is substituted into the dashboard env so its "Pi host"
# card shows the real hostname (otlab-student-NN / otlab-teacher) rather
# than the dashboard container's default name. Falls back to the value
# the operator can override via PI_HOSTNAME env.
PI_HOSTNAME="${PI_HOSTNAME:-$(hostname 2>/dev/null || echo this-pi)}"
echo "    PI_HOSTNAME: $PI_HOSTNAME"

# ESP32 the gateway container polls. Per-student override lives in
# /etc/otlab/student.env (set ESP32_HOST=10.20.30.20X once that student's
# ESP32 is flashed). Falls back to the teacher's ESP32 so freshly-
# deployed students still see real data on day-one before their own
# ESP32 is configured.
ESP32_HOST="${ESP32_HOST:-10.20.30.201}"
echo "    ESP32_HOST:  $ESP32_HOST"

sed \
    -e "s|__DMZ_NET__|$DMZ_NET|g" \
    -e "s|__PCN_NET__|$PCN_NET|g" \
    -e "s|__PI_HOSTNAME__|$PI_HOSTNAME|g" \
    -e "s|__ESP32_HOST__|$ESP32_HOST|g" \
    "$TMPL" > "$OUT"

# ── validate: zero placeholders remain in output ─────────────────────
if grep -qE "__DMZ_NET__|__PCN_NET__|__PI_HOSTNAME__|__ESP32_HOST__" "$OUT"; then
    echo "ERROR: rendered output still contains placeholders:" >&2
    grep -nE "__DMZ_NET__|__PCN_NET__|__PI_HOSTNAME__|__ESP32_HOST__" "$OUT" | head -10 >&2
    [[ "$CHECK" == "yes" ]] && rm -f "$OUT"
    exit 1
fi

# ── validate: yaml is well-formed (only if pyyaml is available) ──────
if command -v python3 >/dev/null 2>&1 \
   && python3 -c "import yaml" 2>/dev/null; then
    if ! python3 -c "import yaml; yaml.safe_load(open('$OUT'))" 2>&1; then
        echo "ERROR: rendered yaml fails to parse" >&2
        exit 1
    fi
    echo "    yaml parse ok"
fi

if [[ "$CHECK" == "yes" ]]; then
    echo "    [check] render is valid; $OUT (will not overwrite)"
    rm -f "$OUT"
else
    echo "    ok — wrote $(wc -l < "$OUT") lines"
fi
