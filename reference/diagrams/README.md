# OTLab diagrams

Source files + rendered PNGs for the lab's visual diagrams.

## Files

| File | Format | What |
|---|---|---|
| `classroom-architecture.svg` + `.png` | SVG + PNG | **Visual** 3-layer classroom architecture (Operator Plane / Classroom Segment / Lab Fabric) with real device icons, color-coded zones, flow lines, and trust annotations. The big-picture view for a 20-student rollout. |
| `purdue-model.svg` + `.png` | SVG + PNG | **Visual** 6-level Purdue Reference Model mapping — every OTLab component placed at its correct Purdue level, with IT/OT divide, DMZ band, and shipped/planned/out-of-scope status badges. |
| `network-architecture.drawio` | drawio | Editable block-diagram source — full network architecture with every asset, current IPs, status, firewall conduits. Open in [app.diagrams.net](https://app.diagrams.net/) or drawio desktop. Older format, kept for editing precision. |

## Which one to use when

| You need... | Use |
|---|---|
| Slide for a talk / pitch deck | `classroom-architecture.png` or `purdue-model.png` (Slack-friendly, looks designed) |
| Edit the visual design | Open the `.svg` in a browser to preview, edit in any text editor or Inkscape |
| Edit the technical block diagram (precise IPs, port numbers) | `network-architecture.drawio` in app.diagrams.net |
| Embed in Notion / docs / wiki | The `.png` (Notion auto-resizes; SVG embeds work in GitHub markdown too) |
| Print as a wall reference | The `.png` at 300 DPI — `qlmanage -t -s 3200 -o . *.svg` then print |

## Editing the SVG diagrams

The SVG files are hand-crafted, fully editable as text. Each is structured:
1. **`<defs>`** at the top — gradients, filters, reusable icon symbols
2. **Title bar** — dark band with title + version
3. **Zone groups** — one `<g>` per layer/level with rounded-rect background + content
4. **Legend** at the bottom

To add a new device or zone, copy an existing one and adjust coordinates. Refresh in browser to preview.

### Re-render PNGs after editing

```sh
# macOS (uses QuickLook — no extra install)
qlmanage -t -s 1600 -o . classroom-architecture.svg purdue-model.svg
# Then rename .svg.png → .png

# Linux (requires librsvg2-bin)
rsvg-convert -w 1600 classroom-architecture.svg -o classroom-architecture.png
rsvg-convert -w 1600 purdue-model.svg -o purdue-model.png
```

## Conventions across diagrams

- **Color-coded zones**: L3 blue/slate, L2 orange, L1 purple (Purdue model uses cool→warm top→bottom)
- **Solid border** = shipped (running today)
- **Dashed border** = planned (V4.x roadmap)
- **Red fill** = firewall / security boundary
- **Status badges** (Purdue diagram): green = shipped, white-dashed = planned, grey = out of scope
- **Real device icons** (not just rectangles): router has antennae, switch has 24 port slots, Pi has CM4 chip + 4 GbE ports, PLC has screen + terminal blocks, sensor has dome+lens, firewall has brick pattern

## Keeping diagrams in sync with the repo

When the architecture changes (new container, IP shift, zone added):

1. Update the master asset table in [`docs/network-architecture.md`](../../docs/network-architecture.md) — source of truth
2. Update [`docs/classroom-network.md`](../../docs/classroom-network.md) if it's a classroom-level change
3. Edit the SVG(s) — re-render PNGs
4. Update the `.drawio` if precision-needed
5. Commit all together so they don't drift

The PNGs are committed alongside SVGs so people without Inkscape/drawio can still see the latest visuals.

## Reference

- Purdue Reference Architecture: Theodore J. Williams, 1989 (Purdue Enterprise Reference Architecture, PERA)
- Updated for ISA-99 / IEC 62443 in the 2000s
- OTLab teaches **L0–L3.5 in depth** (L4 in V4.1) — the OT zones where ICS security actually matters
