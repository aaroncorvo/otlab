# scripts/

Validation scripts, attack/probe scripts, utility tools.

Planned contents (mostly Python, all expected to run inside `~/lab/.venv-modern` on `softplc-2`):

- `validate_honeypots.py` — runs all the cross-Pi checks from the architecture doc end-to-end and prints a green/red status table
- `pump_status_snapshot.py` — pulls live data from all reachable honeypot personas + future real PLCs, formats it as a SCADA-style operator view
- `login_attempts.py` — aggregates honeypot login attempts across personas

Add scripts as you write them. Keep them small and single-purpose.
