# honeypot/

Mirror of `~/conpot/compose/` on `honeypot-host` (Pi 3 B+, lab IP `10.20.30.48`).

Not yet checked in — the live deployment lives on the Pi. To pull a snapshot down for version control:

```bash
# from your laptop
scp -r otadmin@honeypot-host.local:~/conpot/compose/ ./honeypot-snapshot/
# then move the relevant pieces in here, exclude logs/ via .gitignore
```

See [`docs/lab-architecture.md`](../docs/lab-architecture.md#deployment-file-layout) for the full file tree and what each piece does.
