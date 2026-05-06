(*
 * softplc1-sensor-monitor.st - OpenPLC program for softplc-1
 *
 * Phase 1, half 2: closes the Modbus loop between the two real PLCs.
 *
 * Reads the four holding registers and two coils published by sensor-sim
 * on softplc-2 (10.20.30.49:5020), and mirrors them into local variables.
 * Those local variables are exposed automatically on softplc-1 own Modbus
 * TCP server (port 502), so anything else on the network can read the
 * same data via softplc-1.
 *
 * Variable address conventions (OpenPLC + IEC 61131-3):
 *
 *   Slave-device side (incoming from sensor-sim, address 100+):
 *     %IW100..%IW103 - mapped from sensor-sim HR 0..3
 *     %IX100.0       - running flag, mapped from sensor-sim DI 0
 *     %IX100.1       - high-temp alarm, mapped from sensor-sim DI 1
 *
 *   Local outputs (exposed by softplc-1 own Modbus server):
 *     %QW0..%QW5     - holding registers 1..6 on softplc-1:502
 *     %QX0.0/0.1     - coils 1..2 on softplc-1:502
 *
 * Engineering values:
 *   sim_tank      tenths of percent      (587 = 58.7%)
 *   sim_temp      tenths of degree F     (749 = 74.9 F)
 *   sim_press     tenths of PSI          (503 = 50.3 PSI)
 *   sim_hb        seconds since sim start, wraps at 65535
 *
 * The local logic also tracks how long since the heartbeat last advanced.
 * If sensor-sim disappears or its heartbeat freezes, link_loss_count
 * increments and the link_ok flag clears. That is link-liveness
 * monitoring, the kind of thing real SCADA does for every remote endpoint.
 *
 * matiec note: variable declarations cannot carry := default values.
 * Variables initialize to 0 / FALSE by default, which is what we want.
 *)

PROGRAM prog0
    VAR
        (* Inputs from sensor-sim slave (mapped at address 100+) *)
        sim_tank        AT %IW100 : WORD;
        sim_temp        AT %IW101 : WORD;
        sim_press       AT %IW102 : WORD;
        sim_hb          AT %IW103 : WORD;
        sim_running     AT %IX100.0 : BOOL;
        sim_alarm       AT %IX100.1 : BOOL;

        (* Local outputs, exposed via OpenPLC own Modbus server *)
        out_tank        AT %QW0 : WORD;
        out_temp        AT %QW1 : WORD;
        out_press       AT %QW2 : WORD;
        out_hb          AT %QW3 : WORD;
        (* INT not WORD here so arithmetic with int literals is type-clean.
           Modbus exposes %QW as raw 16-bit either way; clients reading these
           registers see the same bytes. *)
        out_link_ok     AT %QW4 : INT;
        out_link_loss   AT %QW5 : INT;
        out_running     AT %QX0.0 : BOOL;
        out_alarm       AT %QX0.1 : BOOL;
    END_VAR

    VAR
        (* Internal state. Kept in a separate VAR block because matiec
           rejects non-located vars mixed in with located ones. *)
        last_hb         : WORD;
        unchanged_scans : INT;
        link_alive      : BOOL;
    END_VAR

    (* Mirror analog values *)
    out_tank  := sim_tank;
    out_temp  := sim_temp;
    out_press := sim_press;
    out_hb    := sim_hb;

    (* Mirror flags *)
    out_running := sim_running;
    out_alarm   := sim_alarm;

    (* Link liveness: if heartbeat has not changed in ~3 seconds, declare
       the link dead and bump the loss counter. Scan is 100ms, 30 scans
       is roughly 3 seconds. *)
    IF sim_hb = last_hb THEN
        unchanged_scans := unchanged_scans + 1;
        IF unchanged_scans > 30 THEN
            IF link_alive THEN
                out_link_loss := out_link_loss + 1;
            END_IF;
            link_alive := FALSE;
        END_IF;
    ELSE
        unchanged_scans := 0;
        last_hb := sim_hb;
        link_alive := TRUE;
    END_IF;

    IF link_alive THEN
        out_link_ok := 1;
    ELSE
        out_link_ok := 0;
    END_IF;
END_PROGRAM


CONFIGURATION Config0
    RESOURCE Res0 ON PLC
        TASK Main(INTERVAL := T#100ms, PRIORITY := 0);
        PROGRAM Inst0 WITH Main : prog0;
    END_RESOURCE
END_CONFIGURATION
