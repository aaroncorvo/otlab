#!/usr/bin/env bash
# install-openplc-program.sh — push an OpenPLC program (.st) to softplc-1
# along with a slave-device config, compile it, and restart the runtime.
# Run from your laptop with the OTLab repo root as cwd.
#
# Usage:
#   ./scripts/install-openplc-program.sh [PI_HOST] [ST_FILE] [SLAVE_NAME] [SLAVE_IP] [SLAVE_PORT] \
#                                        [DI_SIZE] [HR_READ_SIZE]
#
# Defaults match Phase 1: softplc-1 ← sensor-monitor.st polling sensor-sim.

set -euo pipefail

PI_HOST="${1:-otadmin@RASPLC01.local}"   # softplc-1 via mDNS by default; pass user@host to override
ST_FILE="${2:-plc/softplc1-sensor-monitor.st}"
ST_NAME="$(basename "${ST_FILE%.st}").st"          # e.g. sensor-monitor.st
SLAVE_NAME="${3:-sensor-sim}"
SLAVE_IP="${4:-10.20.30.49}"
SLAVE_PORT="${5:-5020}"
DI_SIZE="${6:-2}"
HR_READ_SIZE="${7:-4}"

OPENPLC_DIR='~/OpenPLC_v3/webserver'

echo "==> stop OpenPLC service so DB writes are clean"
ssh "$PI_HOST" 'sudo systemctl stop openplc'

echo "==> push $ST_FILE to $PI_HOST:$OPENPLC_DIR/st_files/$ST_NAME"
scp "$ST_FILE" "$PI_HOST:OpenPLC_v3/webserver/st_files/$ST_NAME"

echo "==> register program in DB + configure slave device + set active program"
ssh "$PI_HOST" "cd OpenPLC_v3/webserver
sqlite3 openplc.db 'INSERT OR REPLACE INTO Programs (Prog_ID, Name, Description, File, Date_upload) VALUES (100, \"$(basename "${ST_FILE%.st}")\", \"Auto-installed by install-openplc-program.sh\", \"$ST_NAME\", \$(date +%s))'
sqlite3 openplc.db 'DELETE FROM Slave_dev'
sqlite3 openplc.db 'INSERT INTO Slave_dev (dev_id, dev_name, dev_type, slave_id, com_port, baud_rate, parity, data_bits, stop_bits, ip_address, ip_port, di_start, di_size, coil_start, coil_size, ir_start, ir_size, hr_read_start, hr_read_size, hr_write_start, hr_write_size, pause) VALUES (1, \"$SLAVE_NAME\", \"TCP\", 1, \"\", 9600, \"None\", 8, 1, \"$SLAVE_IP\", $SLAVE_PORT, 0, $DI_SIZE, 0, 0, 0, 0, 0, $HR_READ_SIZE, 0, 0, 100)'
sqlite3 openplc.db 'UPDATE Settings SET Value = \"true\" WHERE Key = \"Start_run_mode\"'
echo '$ST_NAME' > active_program"

echo "==> compile $ST_NAME"
ssh "$PI_HOST" "cd OpenPLC_v3/webserver && ./scripts/compile_program.sh '$ST_NAME' 2>&1 | tail -8"

echo "==> regenerate mbconfig.cfg (would normally be done by the web UI)"
ssh "$PI_HOST" "cd OpenPLC_v3/webserver && python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('openplc.db'); cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM Slave_dev'); num = int(cur.fetchone()[0])
cur.execute('SELECT Key, Value FROM Settings'); settings = dict(cur.fetchall())
cur.execute('SELECT * FROM Slave_dev'); rows = cur.fetchall()
conn.close()
out  = f'Num_Devices = \"{num}\"\nPolling_Period = \"{settings[\"Slave_polling\"]}\"\nTimeout = \"{settings[\"Slave_timeout\"]}\"\n'
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
PY"

echo "==> start OpenPLC"
ssh "$PI_HOST" 'sudo systemctl start openplc'
sleep 5
ssh "$PI_HOST" 'systemctl is-active openplc'

echo
echo "Done. Verify on the lab segment:"
echo "  python3 -c 'from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient(\"10.20.30.111\",port=502); c.connect(); print(c.read_holding_registers(0,6,device_id=0).registers); c.close()'"
