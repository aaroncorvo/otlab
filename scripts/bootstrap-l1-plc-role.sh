#!/usr/bin/env bash
# bootstrap-l1-plc-role.sh — configure a Pi running OpenPLC for one of the
# lab's defined roles. Idempotent — re-running resets to the canonical
# state in this repo.
#
# Pre-reqs:
#   - Pi OS Lite installed
#   - SSH key auth set up to PI_HOST
#   - OpenPLC v3 installed at ~/OpenPLC_v3 (run scripts/bootstrap-pi.sh first
#     for a from-scratch deployment)
#
# Usage:
#   ./scripts/bootstrap-l1-plc-role.sh PI_HOST ROLE
#
# Args:
#   PI_HOST   user@host, e.g. otadmin@RASPLC01.local
#   ROLE      l1-plc-01 | l1-plc-02
#
# Environment (all optional):
#   OPENPLC_USER      web UI admin username (default: openplc)
#   OPENPLC_PASSWORD  web UI admin password — set this to change the password.
#                     If unset, the script leaves whatever password is already
#                     in the DB. For first-time deployment, set to the lab's
#                     intentionally-public convention (matches MFCTP):
#                       OPENPLC_PASSWORD='P@ssw0rd!' ./bootstrap-l1-plc-role.sh ...
#                     Rotate per DEF CON event so creds don't leak between cohorts.
#
# What it does:
#   1. Stops openplc service so DB writes are clean
#   2. Pins hardware target to "rpi" in OpenPLC's platform file
#   3. (if OPENPLC_PASSWORD set) bcrypts and writes the Users row
#   4. Sets Start_run_mode=true
#   5. Per role:
#      l1-plc-01: deploys plc/softplc1-sensor-monitor.st as sensor-monitor.st,
#                 configures Slave_dev row pointing at sensor-sim on
#                 127.0.0.1:5020 during the l1-plc-02 backfill gap (sensor-sim
#                 is co-located on l1-plc-01 itself; loopback poll). Once
#                 l1-plc-02 backfills, re-run with that as the slave target.
#                 Regenerates mbconfig.cfg.
#      l1-plc-02: pure outstation role (FUTURE backfill) — clears any program +
#                 slave config; sensor-sim runs as a separate Python service
#                 deployed by install-sensor-sim.sh.
#   6. Compiles the program (if loaded)
#   7. Starts openplc and verifies the runtime listens on :502

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@RASPLC01.local}"
ROLE="${2:?ROLE required: l1-plc-01 | l1-plc-02}"

OPENPLC_USER="${OPENPLC_USER:-openplc}"

# Per-role configuration. Add new roles here.
PROGRAM_LOCAL_PATH=""
PROGRAM_REMOTE_NAME=""
SLAVE_NAME=""
SLAVE_IP=""
SLAVE_PORT=""
SLAVE_DI_SIZE=0
SLAVE_HR_READ_SIZE=0
START_RUN_MODE="true"   # whether OpenPLC auto-starts the runtime on boot

case "$ROLE" in
    l1-plc-01)
        PROGRAM_LOCAL_PATH="plc/softplc1-sensor-monitor.st"
        PROGRAM_REMOTE_NAME="sensor-monitor.st"
        SLAVE_NAME="sensor-sim"
        # During the l1-plc-02 backfill gap, sensor-sim runs on l1-plc-01
        # itself (collapsed role). Master polls via loopback. After backfill,
        # re-run this script with SLAVE_IP_OVERRIDE=10.20.30.49 to point at
        # the new outstation.
        SLAVE_IP="${SLAVE_IP_OVERRIDE:-127.0.0.1}"
        SLAVE_PORT=5020
        SLAVE_DI_SIZE=2
        SLAVE_HR_READ_SIZE=4
        ;;
    l1-plc-02)
        # Pure outstation role (backfill). No master program here; sensor-sim
        # runs as a separate Python service deployed by install-sensor-sim.sh.
        # Web UI stays accessible (port 8080); runtime stays dormant
        # (Start_run_mode=false), so :502 doesn't bind for no good reason.
        START_RUN_MODE="false"
        ;;
    *)
        echo "ERROR: unknown role '$ROLE'. Valid: l1-plc-01 | l1-plc-02"
        exit 1
        ;;
esac

echo "==> bootstrapping OpenPLC on $PI_HOST as role '$ROLE'"

# Sanity check
ssh -o BatchMode=yes "$PI_HOST" 'test -d ~/OpenPLC_v3/webserver && test -x ~/OpenPLC_v3/start_openplc.sh' || {
    echo "ERROR: OpenPLC v3 not found at ~/OpenPLC_v3 on $PI_HOST."
    echo "  Run scripts/bootstrap-pi.sh first."
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Stop the service so DB writes are clean
# ---------------------------------------------------------------------------
ssh "$PI_HOST" 'sudo systemctl stop openplc 2>/dev/null || true'

# ---------------------------------------------------------------------------
# 2. Pin hardware target to "rpi"
# ---------------------------------------------------------------------------
echo "==> pinning hardware target = rpi"
ssh "$PI_HOST" 'echo rpi > ~/OpenPLC_v3/webserver/scripts/openplc_platform'

# ---------------------------------------------------------------------------
# 3. (Optional) change web UI password
# ---------------------------------------------------------------------------
if [ -n "${OPENPLC_PASSWORD:-}" ]; then
    echo "==> updating web UI user '$OPENPLC_USER' password"
    # OpenPLC's legacy /login route in webserver.py does a literal plain-text
    # string compare against the Users.password column — see webserver.py
    # 601-640: "if (row[1] == password): ...". No hashing on this path. The
    # REST API in restapi.py uses werkzeug pbkdf2, but the Flask UI form
    # does not. So we must store the password in cleartext for the UI to
    # verify it, terrible as that is. The lab convention is an intentionally-
    # public password (see project notes — matches MFCTP); rotate per DEF CON
    # event so creds don't leak between cohorts.
    ssh "$PI_HOST" "
        cd ~/OpenPLC_v3/webserver
        python3 - <<PYEOF
import sqlite3
conn = sqlite3.connect('openplc.db'); cur = conn.cursor()
cur.execute('UPDATE Users SET password = ? WHERE username = ?', ('$OPENPLC_PASSWORD', '$OPENPLC_USER'))
if cur.rowcount == 0:
    cur.execute('INSERT INTO Users (name, username, email, password, pict_file) VALUES (?, ?, ?, ?, ?)',
                ('Lab Admin', '$OPENPLC_USER', 'lab@example.com', '$OPENPLC_PASSWORD', 'icon-default.png'))
conn.commit(); conn.close()
print(f'  password set for user {repr(\"$OPENPLC_USER\")}')
PYEOF
"
else
    echo "==> OPENPLC_PASSWORD not set — leaving existing password unchanged"
fi

# ---------------------------------------------------------------------------
# 4. Start_run_mode (per role: true for active PLCs, false for dormant ones)
# ---------------------------------------------------------------------------
echo "==> setting Start_run_mode = $START_RUN_MODE"
ssh "$PI_HOST" "cd ~/OpenPLC_v3/webserver && \
    sqlite3 openplc.db 'UPDATE Settings SET Value = \"$START_RUN_MODE\" WHERE Key = \"Start_run_mode\"'"

# ---------------------------------------------------------------------------
# 5. Per-role: program + slave device + active_program
# ---------------------------------------------------------------------------
if [ -n "$PROGRAM_LOCAL_PATH" ]; then
    echo "==> deploying $PROGRAM_LOCAL_PATH as $PROGRAM_REMOTE_NAME"
    scp "$PROGRAM_LOCAL_PATH" "$PI_HOST:~/OpenPLC_v3/webserver/st_files/$PROGRAM_REMOTE_NAME" >/dev/null

    ssh "$PI_HOST" "cd ~/OpenPLC_v3/webserver && \
        sqlite3 openplc.db \"INSERT OR REPLACE INTO Programs (Prog_ID, Name, Description, File, Date_upload) VALUES (100, '$ROLE auto-deploy', 'Installed by bootstrap-l1-plc-role.sh', '$PROGRAM_REMOTE_NAME', \$(date +%s))\" && \
        echo '$PROGRAM_REMOTE_NAME' > active_program"
else
    # No program for this role. We can't just clear active_program — the
    # OpenPLC webserver's run_http() thread crashes at startup if active_program
    # doesn't match a row in the Programs table (TypeError on row[1] when
    # fetchone() returns None), which kills the port-8080 web UI. Point at
    # the always-present Blank Program (prog_id 1, file blank_program.st)
    # so the webserver is happy. With Start_run_mode=false (set above for
    # no-program roles) the runtime stays dormant — no :502 binding.
    echo "==> role $ROLE has no program — pointing active_program at blank_program.st + clearing Programs row 100"
    ssh "$PI_HOST" "cd ~/OpenPLC_v3/webserver && \
        echo 'blank_program.st' > active_program && \
        sqlite3 openplc.db 'DELETE FROM Programs WHERE Prog_ID = 100'"
fi

# Slave_dev table: replace contents with role's slave list
if [ -n "$SLAVE_NAME" ]; then
    echo "==> configuring slave device $SLAVE_NAME @ $SLAVE_IP:$SLAVE_PORT"
    ssh "$PI_HOST" "cd ~/OpenPLC_v3/webserver && \
        sqlite3 openplc.db 'DELETE FROM Slave_dev' && \
        sqlite3 openplc.db \"INSERT INTO Slave_dev (dev_id, dev_name, dev_type, slave_id, com_port, baud_rate, parity, data_bits, stop_bits, ip_address, ip_port, di_start, di_size, coil_start, coil_size, ir_start, ir_size, hr_read_start, hr_read_size, hr_write_start, hr_write_size, pause) VALUES (1, '$SLAVE_NAME', 'TCP', 1, '', 9600, 'None', 8, 1, '$SLAVE_IP', $SLAVE_PORT, 0, $SLAVE_DI_SIZE, 0, 0, 0, 0, 0, $SLAVE_HR_READ_SIZE, 0, 0, 100)\""
else
    echo "==> role $ROLE has no slave devices — clearing Slave_dev"
    ssh "$PI_HOST" "cd ~/OpenPLC_v3/webserver && sqlite3 openplc.db 'DELETE FROM Slave_dev'"
fi

# ---------------------------------------------------------------------------
# 6. Compile (only if there's a program)
# ---------------------------------------------------------------------------
if [ -n "$PROGRAM_REMOTE_NAME" ]; then
    echo "==> compiling $PROGRAM_REMOTE_NAME"
    ssh "$PI_HOST" "cd ~/OpenPLC_v3/webserver && ./scripts/compile_program.sh '$PROGRAM_REMOTE_NAME' 2>&1 | tail -3"
fi

# ---------------------------------------------------------------------------
# 7. Regenerate mbconfig.cfg from current DB state
#    (the web UI does this on Slave Devices form submit; direct DB edits don't
#    trigger it, so the runtime sees zero slave devices unless we regen)
# ---------------------------------------------------------------------------
echo "==> regenerating mbconfig.cfg"
ssh "$PI_HOST" "cd ~/OpenPLC_v3/webserver && python3 - <<'PYEOF'
import sqlite3
conn = sqlite3.connect('openplc.db'); cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM Slave_dev'); num = int(cur.fetchone()[0])
cur.execute('SELECT Key, Value FROM Settings'); settings = dict(cur.fetchall())
cur.execute('SELECT * FROM Slave_dev'); rows = cur.fetchall()
conn.close()
out  = f'Num_Devices = \"{num}\"\nPolling_Period = \"{settings.get(\"Slave_polling\", \"100\")}\"\nTimeout = \"{settings.get(\"Slave_timeout\", \"1000\")}\"\n'
for i, r in enumerate(rows):
    out += f'\n# DEVICE {i}\ndevice{i}.name = \"{r[1]}\"\ndevice{i}.slave_id = \"{r[3]}\"\n'
    out += f'device{i}.protocol = \"TCP\"\ndevice{i}.address = \"{r[9]}\"\n'
    out += f'device{i}.IP_Port = \"{r[10]}\"\n'
    out += f'device{i}.RTU_Baud_Rate = \"{r[5]}\"\ndevice{i}.RTU_Parity = \"{r[6]}\"\n'
    out += f'device{i}.RTU_Data_Bits = \"{r[7]}\"\ndevice{i}.RTU_Stop_Bits = \"{r[8]}\"\ndevice{i}.RTU_TX_Pause = \"{r[21]}\"\n\n'
    out += f'device{i}.Discrete_Inputs_Start = \"{r[11]}\"\ndevice{i}.Discrete_Inputs_Size = \"{r[12]}\"\n'
    out += f'device{i}.Coils_Start = \"{r[13]}\"\ndevice{i}.Coils_Size = \"{r[14]}\"\n'
    out += f'device{i}.Input_Registers_Start = \"{r[15]}\"\ndevice{i}.Input_Registers_Size = \"{r[16]}\"\n'
    out += f'device{i}.Holding_Registers_Read_Start = \"{r[17]}\"\ndevice{i}.Holding_Registers_Read_Size = \"{r[18]}\"\n'
    out += f'device{i}.Holding_Registers_Start = \"{r[19]}\"\ndevice{i}.Holding_Registers_Size = \"{r[20]}\"\n'
open('mbconfig.cfg', 'w').write(out)
print(f'  wrote mbconfig.cfg with {num} device(s)')
PYEOF
"

# ---------------------------------------------------------------------------
# 8. Start service + verify
# ---------------------------------------------------------------------------
echo "==> starting openplc"
ssh "$PI_HOST" 'sudo systemctl start openplc'
sleep 6
echo -n "    systemctl: "
ssh "$PI_HOST" 'systemctl is-active openplc'

echo -n "    Web UI  :8080: "
ssh "$PI_HOST" 'sudo ss -tlnp 2>/dev/null | grep -q :8080 && echo LISTENING || echo NOT-listening'
if [ -n "$PROGRAM_REMOTE_NAME" ]; then
    echo -n "    Modbus  :502:  "
    ssh "$PI_HOST" 'sudo ss -tlnp 2>/dev/null | grep -q :502 && echo LISTENING || echo NOT-listening'
fi

echo
echo "==> bootstrap complete on $PI_HOST as $ROLE"
