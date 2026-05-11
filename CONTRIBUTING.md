# Contributing to OTLab

OTLab is a personal / [ICS Village](https://icsvillage.com/) lab build, made public so others can learn from it, fork it, and improve it. Contributions are welcome — opening issues to share what you've built on top is just as valuable as pull requests.

## Ways to contribute

### Curriculum + exercises *(highest impact)*

The lab has a solid technical foundation; what it needs next is **content**. Specifically:

- **Attack / Detect / Defend exercises** — short, runnable scripts in `plc/tests/` that demonstrate a single ICS technique, mapped to a MITRE ATT&CK for ICS technique ID. Example: T0855 (Unauthorized Command Message) — a Python script that fires a Modbus FC6 from a non-master IP, with an expected Suricata alert + audit-log outcome.
- **Lesson plans** — markdown walkthroughs in `docs/exercises/` for ~30-60 minute teaching sessions. Should have: learning objectives, prereq lab state (single-Pi vs. expanded), step-by-step instructions, expected dashboard reactions, reflection questions.
- **CTF challenges** — flag-based exercises across the existing protocols. Examples: hide a flag in a Conpot HTTP UI, hide a flag in a Modbus register that only opens after a specific sequence, hide a flag in a DNS query the firewall is logging.

If you've taught ICS / OT security before and have material that'd fit, opening a PR (or even just an issue saying "here's how I'd teach this") is super welcome.

### Code contributions

| Area | Examples of useful work |
|---|---|
| **Dashboard front-end** | New visualizations, better mobile layout, accessibility passes, dark/light theme polish |
| **Dashboard back-end** | New `/api/*` endpoints, performance work on large EVE logs, audit-log queries |
| **ContainerLab fabric** | New container types (CODESYS, Ignition Maker, Authentik), additional Conpot personas, additional outstation types |
| **Suricata rules** | More OTLAB-NNNN rules covering additional ICS attack patterns (DNP3 select-before-operate replay, S7comm function code abuse, etc.) |
| **Install / bootstrap** | Better idempotency, more graceful failure recovery, support for additional Pi OS versions or x86 hosts |

### Documentation

- **Tutorials** — "Set up the lab end-to-end with screenshots", "Run your first attack/detect exercise"
- **Video walkthroughs** — screen-recorded demos of specific exercises
- **Blog posts** — write about your experience deploying the lab; link from the README

### Bugs / quality

- Reproducible bug reports against a fresh deploy
- Test fixtures for the dashboard's data parsers (EVE log fixtures, dnsmasq lease fixtures, etc.)
- CI: GitHub Actions for image build smoke tests on PRs

## How to submit

**Issues**: open one for anything — a bug, a question, a suggestion, a "here's what I'd love to see." Tag with `question` / `bug` / `enhancement` / `curriculum` / `documentation` as appropriate.

**Pull requests**: fork → branch → push → open PR against `main`. Keep PRs focused (one feature / fix per PR). Include a short description of what changed and why.

For non-trivial changes, **open an issue first** to discuss the approach — saves you time if there's a better path or if it overlaps with something already in flight.

## Local development

The full lab runs on a single Raspberry Pi 5. See [`docs/setup-from-scratch.md`](docs/setup-from-scratch.md) Stage 1 for setup. Development cycle:

```sh
# 1. Edit code on your laptop
# 2. Push changes to the Pi + rebuild whatever image you changed:
./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
# 3. Test in the dashboard at https://l3-mon-01:8000/
```

For dashboard-only changes (Flask + JS), you don't need a full image rebuild — the dashboard source is bind-mounted, so just push the file and restart the container:

```sh
rsync -a dashboard/ otadmin@l3-mon-01.local:/tmp/dash/
ssh otadmin@l3-mon-01.local '
    sudo rsync -a --chown=otuser:otuser /tmp/dash/ /home/otuser/lab/dashboard/
    sudo docker restart clab-otlab-dashboard'
```

(Note: `docker restart` wipes the dashboard's clab-assigned IP — you may need to `containerlab deploy --reconfigure` to re-apply. Or just run the install script again, which is idempotent.)

## Coding style

- **Python**: PEP 8-ish, prefer stdlib over deps, type hints where they help, docstrings explaining the *why* not the *what*
- **JS**: vanilla, no build step, no framework, no transpiler. Keep it readable in browser dev tools.
- **Shell**: bash 4+, `set -euo pipefail`, prefer `[[ ]]` over `[ ]`, idempotent scripts (safe to re-run)
- **Docs**: clear, opinionated, written for someone setting up the lab for the first time. Code blocks should be copy-pasteable.

The repo has a strong house style of **explaining why decisions were made**, not just what the code does. Match that — future you (and future contributors) will thank you.

## Code of conduct

Be kind. This is a teaching lab; assume the person you're talking to is here to learn or to help, not to score points. If you disagree with someone, disagree with the technical thing they're saying, not with them.

## License

MIT. By contributing you agree your contributions are licensed under the same terms.

## Hardware to test against

We've validated the lab on:

| Hardware | Status |
|---|---|
| Pi 5 16 GB + NVMe SSD via Waveshare PCIe HAT | primary dev platform |
| Pi 5 8 GB + microSD | works for single-Pi mode; tight once Suricata + admin UIs are added |
| Pi 4 8 GB | works for single-Pi mode |
| x86-64 Linux host (Ubuntu 24.04) | not yet tested — would love a PR here |

If you've gotten the lab running on hardware not listed, open an issue with your details — happy to add it.
