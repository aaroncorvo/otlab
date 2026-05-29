(*
 * gateway-mirror.st — OpenPLC Structured Text program.
 *
 * Mirrors the modbus-gateway holding registers (ESP32 sensor data) into
 * named output tags so they show up labeled in the OpenPLC Monitoring
 * tab and can drive ladder logic / alarms / HMI screens.
 *
 * Wiring (set by scripts/openplc-add-gateway-slave.sh):
 *   Gateway HR[0..5] -> %IW100..%IW105
 *
 * Register map (see gateway/README.md):
 *   HR[0]  uptime_high       unsigned (uptime_s >> 16)
 *   HR[1]  uptime_low        unsigned (uptime_s & 0xFFFF)
 *   HR[2]  mcu_temp_x10      signed   (deg C * 10)
 *   HR[3]  wifi_rssi         signed   (dBm)
 *   HR[4]  status_flag       unsigned (1 = fresh, 0 = stale/error)
 *   HR[5]  last_poll_age_ms  unsigned (ms since last successful poll)
 *
 * To load in OpenPLC:
 *   1. Browse to http://<pi-ip>:8081
 *   2. Programs -> Choose File -> select this .st
 *   3. Upload Program -> set "Compile" to Yes
 *   4. Click Start PLC (top right)
 *
 * Then watch values live under Monitoring (refresh every 1 s).
 *)

PROGRAM gateway_mirror
  VAR
    (* Inputs: pulled from modbus-gateway via the Slave_dev entry *)
    in_uptime_hi    AT %IW100 : WORD;
    in_uptime_lo    AT %IW101 : WORD;
    in_temp_x10     AT %IW102 : INT;     (* signed *)
    in_rssi         AT %IW103 : INT;     (* signed *)
    in_status       AT %IW104 : WORD;
    in_poll_age_ms  AT %IW105 : WORD;

    (* Derived values for HMI / monitoring *)
    out_uptime_s    : DINT;
    out_temp_c_x10  : INT;   (* re-export signed temp *)
    out_rssi_dbm    : INT;
    out_fresh       : BOOL;
    out_stale_ms    : WORD;

    (* Alarm flags — drive lights or sirens from these *)
    alarm_stale     : BOOL;
    alarm_hot       : BOOL;
    alarm_weak_wifi : BOOL;
  END_VAR

  (* Combine high+low into 32-bit uptime *)
  out_uptime_s := SHL(DINT#TO_DINT(in_uptime_hi), 16) OR DINT#TO_DINT(in_uptime_lo);
  out_temp_c_x10 := in_temp_x10;
  out_rssi_dbm   := in_rssi;
  out_fresh      := (in_status = 1);
  out_stale_ms   := in_poll_age_ms;

  (* Simple alarm logic — tune thresholds for your room *)
  alarm_stale     := NOT out_fresh OR (out_stale_ms > 5000);
  alarm_hot       := (out_temp_c_x10 > 450);   (* 45.0 C *)
  alarm_weak_wifi := (out_rssi_dbm < -75);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK Main(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM Inst0 WITH Main : gateway_mirror;
  END_RESOURCE
END_CONFIGURATION
