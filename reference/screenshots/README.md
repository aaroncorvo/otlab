# Dashboard screenshots

PNG captures of the dashboard's seven tabs, used in the project README +
docs to show prospective users what the lab looks like.

## Capture

The fastest path is the helper script:

```sh
./scripts/capture-screenshots.sh
```

It opens Chrome at the dashboard, waits for you to log in once, then
cycles through the seven tabs (Overview → Architecture → IDS →
Firewall → DHCP → Live Data → Teaching) capturing one PNG per tab.

If you'd rather do it by hand: log into the dashboard in your browser,
visit each tab, wait ~5 seconds for data to refresh, take a full-window
screenshot (Cmd-Shift-4 then Space, then click the window on macOS),
save each as `<tab>.png` here.

## Files

| File | Tab | What's in it |
|---|---|---|
| `overview.png` | Overview | Live process synoptic + all infra/PCN cards |
| `architecture.png` | Architecture | Purdue model + network topology |
| `ids.png` | IDS | Suricata counts + timeline + top sigs |
| `firewall.png` | Firewall | Iptables FORWARD chain + DNS stats |
| `dhcp.png` | DHCP | Per-zone lease + reservation tables |
| `live-data.png` | Live Data | System health + wire feed + audit |
| `teaching.png` | Teaching | Risks, Test Library, Write Playground |

## Demo GIF

Animated capture of a demo flow (e.g. firing a FC6 write and watching
the IDS tab fire an alert) lives at `reference/screenshots/demo.gif`.

To record:

1. Open the dashboard in Chrome at the **Overview** tab
2. On macOS: Cmd-Shift-5 → "Record Selected Portion" → frame the
   Chrome window → "Options" → set save location to this directory
3. Walk through ~30 seconds of:
   - Show the Overview tab with live process data ticking
   - Switch to Teaching → fire a FC6 write via the Write Playground
   - Switch to IDS tab → show the OTLAB-1004 alert appearing
   - Switch to Firewall tab → show the SNAT rule packet counter ticking
4. Stop recording (Esc) — saves as a `.mov`
5. Convert to GIF with `ffmpeg`:
   ```sh
   ffmpeg -i Screen\ Recording*.mov -vf "fps=12,scale=1200:-1:flags=lanczos" \
          -loop 0 demo.gif
   rm Screen\ Recording*.mov
   ```

Target: <10 MB GIF so GitHub's README renders it inline.
