#!/usr/bin/env bash
# configure-virtual-plc.sh — configure a virtual OpenPLC container
# (clab-otlab-plc-{N}-virt) for one of the lab roles. Sister to
# bootstrap-l1-plc-role.sh, but targets a containerlab-managed OpenPLC
# container via 'docker exec' instead of SSHing to a physical Pi.
#
# Idempotent — re-running resets the container's role to canonical state.
#
# Usage:
#   ./scripts/configure-virtual-plc.sh PI_HOST CONTAINER ROLE [SLAVE_IP]
#
# Args:
#   PI_HOST     ssh user@host of the virt host (l3-mon-01)
#   CONTAINER   docker container name (e.g. clab-otlab-plc-1-virt)
#   ROLE        master | outstation
#               master      → runs softplc1-sensor-monitor.st, polls a slave
#               outstation  → no master program; sits idle (legacy / future)
#   SLAVE_IP    (master role only) IP of the Modbus slave to poll
#               default: 10.20.30.70 (sensor-sim virtual node in V1 topology)
#
# Environment (all optional):
#   OPENPLC_PASSWORD  web UI password — defaults to 'P@ssw0rd!' (lab convention)
#
# What it does (master role):
#   1. Stops the openplc runtime (kills any python3 -m openplc inside)
#   2. Pins hardware target = "rpi"
#   3. Sets web UI password (cleartext per OpenPLC v3's compare logic)
#   4. Sets Start_run_mode=true
#   5. Copies plc/softplc1-sensor-monitor.st into the container
#   6. Inserts/updates Programs row + active_program pointer
#   7. Configures Slave_dev for sensor-sim @ <SLAVE_IP>:5020
#   8. Compiles the program
#   9. Regenerates mbconfig.cfg
#  10. Restarts the container so the new config takes effect
#
# Pre-reqs:
#   - V1 ContainerLab topology deployed (containers running)
#   - Repo root checked out on the laptop (so plc/softplc1-sensor-monitor.st
#     is rsync-able onto the Pi)

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@l3-mon-01.local}"
CONTAINER="${2:?CONTAINER required, e.g. clab-otlab-plc-1-virt}"
ROLE="${3:?ROLE required: master | outstation}"
SLAVE_IP="${4:-10.20.30.70}"

OPENPLC_USER="${OPENPLC_USER:-openplc}"
OPENPLC_PASSWORD="${OPENPLC_PASSWORD:-P@ssw0rd!}"

echo "==> configuring $CONTAINER on $PI_HOST as role '$ROLE'"

# ---------------------------------------------------------------------------
# Sanity check — container must be running
# ---------------------------------------------------------------------------
ssh "$PI_HOST" "sudo docker inspect -f '{{.State.Running}}' $CONTAINER" 2>&1 \
    | grep -q true \
    || { echo "ERROR: container $CONTAINER is not running"; exit 1; }

case "$ROLE" in
    master)
        # Stage the .st program onto the Pi (so we can docker cp it in)
        echo "==> staging softplc1-sensor-monitor.st onto $PI_HOST"
        scp plc/softplc1-sensor-monitor.st "$PI_HOST:/tmp/sensor-monitor.st" >/dev/null

        echo "==> stopping any running OpenPLC runtime inside $CONTAINER"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER bash -c 'pkill -f openplc_runtime 2>/dev/null || true'"

        echo "==> pinning hardware target = rpi"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER bash -c 'echo rpi > /opt/OpenPLC_v3/webserver/scripts/openplc_platform'"

        echo "==> setting web UI password (lab convention)"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER sqlite3 /opt/OpenPLC_v3/webserver/openplc.db \
            \"UPDATE Users SET password = '$OPENPLC_PASSWORD' WHERE username = '$OPENPLC_USER'\""

        echo "==> Start_run_mode = true"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER sqlite3 /opt/OpenPLC_v3/webserver/openplc.db \
            \"UPDATE Settings SET Value = 'true' WHERE Key = 'Start_run_mode'\""

        echo "==> copying sensor-monitor.st into container st_files/"
        ssh "$PI_HOST" "sudo docker cp /tmp/sensor-monitor.st $CONTAINER:/opt/OpenPLC_v3/webserver/st_files/sensor-monitor.st && rm /tmp/sensor-monitor.st"

        echo "==> registering Program row + active_program pointer"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER sqlite3 /opt/OpenPLC_v3/webserver/openplc.db \
            \"INSERT OR REPLACE INTO Programs (Prog_ID, Name, Description, File, Date_upload) VALUES (100, 'sensor-monitor (master)', 'Master polling sensor-sim @ ${SLAVE_IP}:5020', 'sensor-monitor.st', strftime('%s','now'))\""
        ssh "$PI_HOST" "sudo docker exec $CONTAINER bash -c 'echo sensor-monitor.st > /opt/OpenPLC_v3/webserver/active_program'"

        echo "==> configuring Slave_dev: sensor-sim @ ${SLAVE_IP}:5020"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER sqlite3 /opt/OpenPLC_v3/webserver/openplc.db \
            \"DELETE FROM Slave_dev; INSERT INTO Slave_dev (dev_id, dev_name, dev_type, slave_id, com_port, baud_rate, parity, data_bits, stop_bits, ip_address, ip_port, di_start, di_size, coil_start, coil_size, ir_start, ir_size, hr_read_start, hr_read_size, hr_write_start, hr_write_size, pause) VALUES (1, 'sensor-sim', 'TCP', 1, '', 9600, 'None', 8, 1, '${SLAVE_IP}', 5020, 0, 2, 0, 0, 0, 0, 0, 4, 0, 0, 100)\""

        echo "==> compiling sensor-monitor.st"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER bash -c 'cd /opt/OpenPLC_v3/webserver && ./scripts/compile_program.sh sensor-monitor.st 2>&1 | tail -3'"

        echo "==> regenerating mbconfig.cfg"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER python3 - <<'PYEOF'
import sqlite3
conn = sqlite3.connect('/opt/OpenPLC_v3/webserver/openplc.db'); cur = conn.cursor()
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
open('/opt/OpenPLC_v3/webserver/mbconfig.cfg', 'w').write(out)
print(f'  wrote mbconfig.cfg with {num} device(s)')
PYEOF
"
        ;;

    outstation)
        echo "==> outstation role: clearing program + slave config + Start_run_mode=false"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER bash -c 'echo blank_program.st > /opt/OpenPLC_v3/webserver/active_program'"
        ssh "$PI_HOST" "sudo docker exec $CONTAINER sqlite3 /opt/OpenPLC_v3/webserver/openplc.db \
            \"UPDATE Settings SET Value = 'false' WHERE Key = 'Start_run_mode'; DELETE FROM Slave_dev; DELETE FROM Programs WHERE Prog_ID = 100\""
        ;;

    *)
        echo "ERROR: unknown role '$ROLE'. Valid: master | outstation"
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Restart the container so the new config + program take effect
# ---------------------------------------------------------------------------
echo "==> restarting $CONTAINER"
ssh "$PI_HOST" "sudo docker restart $CONTAINER >/dev/null"
sleep 6

echo "==> verifying"
ssh "$PI_HOST" "sudo docker exec $CONTAINER bash -c '
    echo \"--- openplc.service / runtime check ---\"
    ss -tlnp 2>/dev/null | grep -E \":(502|8080|8443)\" || echo \"(no expected ports listening yet — webserver may still be starting)\"
    echo
    echo \"--- last 5 lines of webserver log ---\"
    tail -5 /opt/OpenPLC_v3/webserver/openplc.log 2>/dev/null || echo \"(no log file yet)\"
'"

cat <<EOF

==============================================================================
 $CONTAINER configured as $ROLE.

 Web UI:   http://${PI_HOST##*@}:8081/  (login: $OPENPLC_USER / $OPENPLC_PASSWORD)
            (port 8081 if plc-1-virt; 8082 if plc-2-virt — see topology.clab.yaml)

 Runtime: ${ROLE^} role $([ "$ROLE" = "master" ] && echo "polls $SLAVE_IP:5020 every 100 ms")

 Verify the loop (master role):
   - From the dashboard host: tcpdump -i pcn-br0 'tcp port 5020' should show
     SYN/ACK + Modbus FC2/FC3 every 100 ms once the runtime starts
   - Browse to the OpenPLC web UI — Monitoring tab should show live values
==============================================================================
EOF
