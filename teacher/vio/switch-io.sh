#!/usr/bin/env bash
# switch-io.sh — flip a Pi between physical Qwiic I/O and the virtual
# simulated-turbine backend on :8090. The PLC (otlab-plc) and Modbus bridge
# (otlab-modbus-io) keep pointing at :8090, so nothing else changes — they
# just drive whichever backend is active.
#
# Usage:
#   ./teacher/vio/switch-io.sh otadmin@10.20.30.49 virtual
#   ./teacher/vio/switch-io.sh otadmin@10.20.30.49 physical
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.49}"
MODE="${2:?MODE required: virtual | physical}"
case "$MODE" in virtual|physical) ;; *) echo "MODE must be 'virtual' or 'physical'"; exit 1;; esac

echo "==> switching ${PI_HOST#*@} to ${MODE} I/O on :8090"
ssh "$PI_HOST" "sudo MODE='$MODE' bash -s" <<'REMOTE'
set -e
if [ "$MODE" = "virtual" ]; then
    systemctl disable --now otlab-qwiic.service 2>/dev/null || true
    systemctl enable  --now otlab-vio.service
else
    systemctl disable --now otlab-vio.service 2>/dev/null || true
    systemctl enable  --now otlab-qwiic.service
fi
# Re-seed the consumers against the freshly-selected backend.
systemctl restart otlab-plc.service        2>/dev/null || true
systemctl restart otlab-modbus-io.service  2>/dev/null || true
sleep 2
echo "  :8090 owner -> qwiic=$(systemctl is-active otlab-qwiic 2>/dev/null) vio=$(systemctl is-active otlab-vio 2>/dev/null)"
echo "  plc=$(systemctl is-active otlab-plc 2>/dev/null) modbus=$(systemctl is-active otlab-modbus-io 2>/dev/null)"
REMOTE

echo "==> done. I/O page: http://${PI_HOST#*@}:8090/   PLC: http://${PI_HOST#*@}:8091/"
