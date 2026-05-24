# OTLab — Classroom SIEM (Loki + Grafana + Promtail)

Centralised log aggregation + dashboards + alerting for a 20-student
classroom. Runs on the teacher host alongside the teacher admin panel.

## What it does

- Receives logs from up to 20 student Pis (Suricata EVE JSON, dashboard
  nginx logs, firewall iptables logs)
- Indexes by `student_id` label so the instructor can filter / group /
  alert per student
- Stores 7 days of logs by default (configurable)
- Provides a default classroom dashboard + raw log explorer
- Open for alert rules (Grafana Alerting → Loki ruler)

Lightweight by design — chosen over Wazuh/Graylog because it fits a
single laptop/Pi sized teacher host (~2 GB RAM steady state).

## Quick start

```sh
cd teacher/siem
docker compose up -d
```

Browse:
- Grafana UI:  `http://<teacher-ip>:3000`  (login `admin` / `P@ssw0rd!`)
- Loki direct: `http://<teacher-ip>:3100/ready`  (should return `ready`)

Wait a minute, then check the "OTLab — Classroom Overview" dashboard.
You'll see logs flowing from any student Pi running the Promtail agent
(see `teacher/agents/install-student-promtail.sh`).

## Endpoints

| Port | Service | Who calls it |
|---|---|---|
| `3100` | Loki HTTP — push + query API | Student Pis (push); Grafana (query) |
| `3000` | Grafana UI + API | Instructor laptop |
| `9080` | Teacher-side Promtail HTTP debug | Local only |
| `9096` | Loki gRPC (internal) | Loki cluster (single-node — unused) |

## What students ship

Each student Pi runs `promtail-otlab.service` (installed by
`teacher/agents/install-student-promtail.sh` during the classroom
install). It scrapes:

| Source | Loki labels |
|---|---|
| `/var/log/suricata/eve.json` | `job=suricata`, `event_type=alert/anomaly/flow/dns/...`, `student_id`, `host` |
| `/var/lib/otlab/fw-state/dnsmasq-fw.log` | `job=dnsmasq`, `student_id`, `host` |
| Dashboard container stdout | `job=otlab-dashboard`, `student_id`, `host` |
| Firewall container stdout | `job=otlab-firewall`, `student_id`, `host` |
| System journal | `job=syslog`, `student_id`, `host` |

`student_id` is the integer student # (1-20) from
`/etc/otlab/student.env` on the student Pi.

## Default dashboards

Provisioned automatically from `grafana/dashboards/`:

| Dashboard | What it shows |
|---|---|
| **OTLab — Classroom Overview** | Log volume per student (last 30 min); Suricata alert count per student; top signatures; recent firewall denies |

Add more by dropping JSON in `grafana/dashboards/` — Grafana picks up
new files every 30s.

## Alerting (future PR)

Loki ruler is enabled and reads `/loki/rules/` (mounted as `loki-data`
volume). Rule format is Prometheus-style alerting rules over LogQL.

Example rule (to be written):

```yaml
groups:
  - name: otlab-suricata
    rules:
      - alert: HighSuricataAlertRate
        expr: |
          sum by (student_id) (
            rate({job="suricata", event_type="alert"}[5m])
          ) > 1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Student {{ $labels.student_id }} firing > 60 Suricata alerts/min"
```

Tracked as a follow-up task.

## State + reset

| What | Where | Wipe |
|---|---|---|
| Indexed logs (7d default) | `otlab-siem-loki-data` Docker volume | `docker compose down -v` |
| Grafana dashboards, users, settings | `otlab-siem-grafana-data` Docker volume | `docker compose down -v` (you'll lose any custom dashboards not in `grafana/dashboards/`) |
| Promtail position file | `otlab-siem-promtail-data` Docker volume | safe to delete |

For end-of-class wipe:
```sh
docker compose down -v
docker compose up -d   # back to clean state, dashboards re-provisioned
```

## Environment overrides

| Var | Default | Purpose |
|---|---|---|
| `GRAFANA_ADMIN_USER` | `admin` | Grafana admin login |
| `GRAFANA_ADMIN_PASS` | `P@ssw0rd!` | Grafana admin password — **rotate per event** |

## Why Loki and not Wazuh/Graylog

Decision rationale captured for future maintainers:

| Option | Why we didn't pick it |
|---|---|
| **Wazuh** | ~6 GB RAM minimum. Heavy install. Agent install per student. Pedagogically richest but the teacher box would need a beefy laptop. Best choice if/when the project moves to per-student dedicated server hardware. |
| **Graylog** | ~4 GB RAM. Solid log aggregation but ICS-specific dashboards aren't OOTB. We'd be building most of the value ourselves. |
| **Loki + Grafana + Promtail** | ✅ ~2 GB RAM. Already familiar (Grafana is widespread). Per-student labels make filtering trivial. Promtail is small enough to run on every student Pi without measurable impact. |
| **Custom rsyslog → SQLite → teacher panel tab** | Cheapest but no exploration UI, no alerting beyond what we'd hand-build. Reserved for if/when we want a single-process teacher stack. |

## See also

- `teacher/agents/install-student-promtail.sh` — student-side log shipper installer
- `teacher/agents/promtail-student.yml.tmpl` — student-side config template (rendered from `/etc/otlab/student.env`)
- `docs/classroom-installer.md` — full classroom install walkthrough
- `docs/classroom-network.md` — network map
