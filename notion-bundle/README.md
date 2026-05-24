# OTLab — Notion Bundle (ICS Village workspace)

Paste-ready Notion documentation for the ICS Village workspace.
9 markdown files covering everything operational about the OTLab —
hardware kit, classroom rollout, install + reset, network map, SIEM,
trust model, pre-event checklist.

## What's in here

| # | File | What it covers |
|---|---|---|
| 00 | `00-OTLab-Index.md` | Landing page — what lives where, credentials, trust model, links to the other 8 |
| 01 | `01-Single-Pi-Setup.md` | End-to-end build of one student kit |
| 02 | `02-Teacher-Admin-Panel.md` | Instructor's dashboard reference |
| 03 | `03-Architecture-Reference.md` | Zones, subnets, per-student plan, asset inventory |
| 04 | `04-Pre-Event-Checklist.md` | Before / during / after a class |
| 05 | `05-Classroom-Network-Map.md` | Network architecture across all Pis |
| 06 | `06-Classroom-Installer-Reset.md` | Install + reset script walkthrough |
| 07 | `07-Classroom-SIEM.md` | Loki + Grafana + Promtail stack |
| 08 | `08-Hardware-Kit-Cruiser-Cisco.md` | Production hardware spec (~$6,930 for 20-student build) |

## How to import into Notion (ICS Village workspace)

### One-time setup

1. Open the ICS Village Notion workspace
2. Pick (or create) a parent page where OTLab docs should live — e.g. a teamspace page called **OTLab**
3. Open that page

### Drag-import (recommended)

1. Open Finder, navigate to `notion-bundle/` (this folder)
2. Select **all 9 .md files** (skip this README)
3. Drag them into the open Notion page
4. Notion creates 9 child pages, one per file, importing the markdown (tables, headings, code blocks, lists all preserved)
5. Drag `00-OTLab-Index` to the top of the list (it's the landing page)
6. Rename pages as desired — the filename prefix `00-`, `01-` etc. is just for sort order in the bundle; Notion titles come from the H1 inside each file

### Manual paste (alternative — for one page at a time)

1. In Notion, create a new empty page
2. Open the corresponding `.md` file in any text editor
3. Copy the entire contents (Cmd+A → Cmd+C)
4. Paste into the empty Notion page (Cmd+V)
5. Notion auto-converts markdown syntax on paste

## Updating later

These files mirror the canonical docs in the repo:

| Bundle file | Repo source of truth |
|---|---|
| `00-OTLab-Index` | (Notion-only, no repo equivalent) |
| `01-Single-Pi-Setup` | `docs/setup-from-scratch.md` |
| `02-Teacher-Admin-Panel` | `teacher/README.md` |
| `03-Architecture-Reference` | `docs/network-architecture.md` |
| `04-Pre-Event-Checklist` | `docs/classroom-network.md` (Pre-event section) |
| `05-Classroom-Network-Map` | `docs/classroom-network.md` |
| `06-Classroom-Installer-Reset` | `docs/classroom-installer.md` |
| `07-Classroom-SIEM` | `teacher/siem/README.md` |
| `08-Hardware-Kit-Cruiser-Cisco` | `docs/classroom-network.md` (hardware kit section) |

When repo docs change significantly, regenerate the bundle and re-import
the changed pages. To regenerate, run:

```sh
# (placeholder — bundle is hand-curated for now, not auto-generated)
# Eventually: scripts/build-notion-bundle.sh
```

## Why a bundle and not direct Notion automation?

Notion MCP integrations are scoped to a single workspace. The MCP
authorized for Claude is bound to Aaron's personal workspace, not the
ICS Village teamspace. Until the Notion connector is added to the ICS
Village workspace, the bundle is the bridge.

When/if that connector is added, future doc updates can flow directly
into ICS Village without re-importing — but the bundle still serves as
offline documentation (useful at events with bad connectivity).
