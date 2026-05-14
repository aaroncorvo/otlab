# OTLab diagrams

Source files for the lab's architecture diagrams. Open in
[app.diagrams.net](https://app.diagrams.net/) (web) or the
[drawio desktop app](https://www.drawio.com/) (free, all OSes).

## Files

| File | What |
|---|---|
| `network-architecture.drawio` | Full network architecture — 3 zones (L4 Enterprise / L3.5 DMZ / L1/L2 PCN) with every asset, current IPs, status (shipped vs. planned), and firewall conduits |

## Convention

- **Solid border** = shipped (running today)
- **Dashed border** = planned (V4.x roadmap)
- **Red fill** = firewall / security boundary
- **Bold border** = key node (dashboard, modbus-master, sensor-sim)
- **Zone colors** match the dashboard's Architecture tab:
  - Orange = L4 Enterprise (Untrusted)
  - Blue = L3.5 DMZ (Operations)
  - Green = L1/L2 PCN (Process Control)
  - Gray (dashed) = host services (outside ContainerLab)

## Editing

1. Open the `.drawio` file at https://app.diagrams.net/
   (File → Open from → Device, or drag-and-drop)
2. Edit visually
3. Save back to the file (Cmd-S / Ctrl-S)
4. Commit the updated `.drawio` to the repo
5. Optionally export PNG/SVG for slides:
   File → Export as → PNG / SVG / PDF

The Mermaid version embedded in
[`docs/network-architecture.md`](../../docs/network-architecture.md)
renders inline on GitHub — quicker for casual viewing, less detailed than
this drawio source.

## Keeping the diagrams in sync

When the topology changes (new container, IP shift, zone added):

1. Update the master asset table in [`docs/network-architecture.md`](../../docs/network-architecture.md) — that's the source of truth
2. Update the Mermaid diagram in the same doc
3. Update this `.drawio` file
4. Commit all three together so they don't drift
