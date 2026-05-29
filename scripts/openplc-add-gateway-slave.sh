#!/usr/bin/env bash
# openplc-add-gateway-slave.sh
#
# Adds the modbus-gateway as a "Generic Modbus TCP Device" inside the
# plc-1-virt OpenPLC container, so OpenPLC's runtime polls HR[0..5] from
# the ESP32 bridge and surfaces them as %IW0..%IW5 in ladder/ST programs.
#
# Idempotent: re-running re-INSERTs only if no row with the same
# (dev_name, ip_address) exists.
#
# Usage on a Pi:
#   sudo bash scripts/openplc-add-gateway-slave.sh
#
# Reads the PCN subnet from /etc/otlab/student.env (STUDENT_PCN_NET).
# Falls back to the single-Pi default 10.20.30 if no env file is found.
#
# After this runs, finish the wiring inside the OpenPLC web UI:
#   1. Browse to http://<pi-ip>:8081
#   2. Log in (openplc / P@ssw0rd!)
#   3. Slave Devices -> confirm the "modbus-gateway" row appears
#   4. Programs -> Upload a ST program that reads %IW100..%IW105
#      (the gateway data lands there; see configs/openplc/gateway-mirror.st)
#   5. Click Start PLC

set -euo pipefail

CONTAINER="${CONTAINER:-clab-otlab-plc-1-virt}"
DB_PATH="${DB_PATH:-/opt/OpenPLC_v3/webserver/openplc.db}"

# Resolve the gateway IP from per-student env, or default for single-Pi.
PCN_NET="10.20.30"
if [ -f /etc/otlab/student.env ]; then
    # shellcheck disable=SC1091
    . /etc/otlab/student.env
    if [ -n "${STUDENT_PCN_NET:-}" ]; then
        PCN_NET="$STUDENT_PCN_NET"
    fi
fi
GATEWAY_IP="${PCN_NET}.180"

echo "==> seeding OpenPLC Slave_dev with modbus-gateway at ${GATEWAY_IP}:502"

# Ensure the container is running.
if ! sudo docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
    echo "ERROR: container $CONTAINER is not running. Deploy the clab topology first." >&2
    exit 1
fi

# OpenPLC's DB is created on first webserver run. Hit the index once to
# guarantee it exists (idempotent).
sudo docker exec "$CONTAINER" sh -c \
    "test -f $DB_PATH || (cd /opt/OpenPLC_v3/webserver && timeout 3 python3 webserver.py >/dev/null 2>&1 &); sleep 2; test -f $DB_PATH" \
    || true

# Confirm the DB exists now; bail if not.
if ! sudo docker exec "$CONTAINER" test -f "$DB_PATH"; then
    echo "ERROR: $DB_PATH was not created. OpenPLC webserver may not have run yet." >&2
    echo "       Open http://<pi-ip>:8081 once to initialize the DB, then re-run." >&2
    exit 2
fi

# Idempotency check: only insert if no row with this dev_name+ip exists.
existing=$(sudo docker exec "$CONTAINER" sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM Slave_dev WHERE dev_name='modbus-gateway' AND ip_address='${GATEWAY_IP}';" 2>/dev/null || echo 0)

if [ "$existing" -gt 0 ]; then
    echo "    already configured (dev_name='modbus-gateway', ip='${GATEWAY_IP}') — no-op"
    exit 0
fi

# Insert. Column meanings (OpenPLC v3 Slave_dev — actual schema):
#   dev_name           free-form label shown in the UI (UNIQUE)
#   dev_type           must be 'Generic Modbus TCP Device' for TCP polling
#   slave_id           Modbus unit ID (gateway uses single=True so any ID works)
#   com_port           NULL for TCP (we set 'COM1' as a placeholder since
#                      some OpenPLC code paths fail open on NULL)
#   baud_rate/parity/data_bits/stop_bits  unused for TCP, placeholders
#   ip_address/ip_port TCP target
#   *_start, *_size    Modbus address window per data type (0/0 = disabled)
#   pause              0 = active, 1 = paused (skip polling)
#
# IO map placement: OpenPLC v3 reserves %IW100+ for slave-device reads;
# with this as the only slave, gateway HR[0..5] lands at %IW100..%IW105.
# (See virtual/configs/openplc/gateway-mirror.st for a ready-made ST
# program that picks them up.)
if ! sudo docker exec -i "$CONTAINER" sqlite3 "$DB_PATH" <<SQL
INSERT INTO Slave_dev (
    dev_name, dev_type, slave_id,
    com_port, baud_rate, parity, data_bits, stop_bits,
    ip_address, ip_port,
    di_start, di_size,
    coil_start, coil_size,
    ir_start, ir_size,
    hr_read_start, hr_read_size,
    hr_write_start, hr_write_size,
    pause
) VALUES (
    'modbus-gateway', 'Generic Modbus TCP Device', 1,
    'COM1', 9600, 'None', 8, 1,
    '${GATEWAY_IP}', 502,
    0, 0,
    0, 0,
    0, 0,
    0, 6,
    0, 0,
    0
);
SQL
then
    echo "ERROR: INSERT failed. Re-run with sqlite3 verbose: sudo docker exec ${CONTAINER} sqlite3 ${DB_PATH}" >&2
    exit 3
fi

# Verify the row landed (paranoid check; sqlite3's CLI exit status is
# unreliable in some pipelines).
inserted=$(sudo docker exec "$CONTAINER" sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM Slave_dev WHERE dev_name='modbus-gateway' AND ip_address='${GATEWAY_IP}';" 2>/dev/null || echo 0)
if [ "$inserted" -eq 0 ]; then
    echo "ERROR: Slave_dev row is missing after INSERT — check container logs." >&2
    exit 4
fi
echo "    OK. Slave_dev row inserted (1 row now matches)."
echo
echo "Next steps inside the OpenPLC web UI (http://<pi-ip>:8081):"
echo "  1. Slave Devices  -> confirm 'modbus-gateway' row exists"
echo "  2. Programs        -> upload virtual/configs/openplc/gateway-mirror.st"
echo "  3. Hardware        -> confirm 'Modbus TCP Slave' driver is enabled"
echo "  4. Click 'Start PLC' (top right)"
echo
echo "Verify in container logs:"
echo "  sudo docker logs -f ${CONTAINER} 2>&1 | grep -i 'slave\\|modbus'"
