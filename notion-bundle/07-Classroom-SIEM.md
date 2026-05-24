# Classroom SIEM (Loki + Grafana + Promtail)

Centralised log aggregation + dashboards + alerting for a 20-student classroom. Runs on the teacher host alongside the teacher admin panel.

> **Repo source of truth**: [`teacher/siem/README.md`](https://github.com/aaroncorvo/otlab/blob/main/teacher/siem/README.md)

## What it does

- Receives logs from up to 20 student Pis (Suricata EVE JSON, dashboard nginx logs, firewall iptables logs)
- Indexes by `student_id` label so the instructor can filter / group / alert per student
- Stores 7 days of logs by default (configurable)
- Provides a default classroom dashboard + raw log explorer
- Ready for Grafana alerting rules

Lightweight by design — ~2 GB RAM steady state. Fits a laptop teacher host.

## Quick start

```
cd teacher/siem
docker compose up -d
```

Browse:
- Grafana UI: `http://<teacher-ip>:3000` (login `admin` / `P@ssw0rd!`)
- Loki direct: `http://<teacher-ip>:3100/ready` (returns `ready`)

## Endpoints

| Port | Service | Who calls it |
|---|---|---|
| 3100 | Loki HTTP — push + query API | Student Pis (push), Grafana (query) |
| 3000 | Grafana UI + API | Instructor laptop |
| 9080 | Teacher-side Promtail debug | Local only |
| 9096 | Loki gRPC (internal) | Loki cluster (single-node, unused) |

## What students ship

Each student Pi runs `promtail-otlab.service` (installed by `teacher/agents/install-student-promtail.sh` during the classroom install). It scrapes:

| Source | Loki labels |
|---|---|
| `/var/log/suricata/eve.json` | `job=suricata`, `event_type=alert/anomaly/flow/dns/…`, `student_id`, `host` |
| `/var/lib/otlab/fw-state/dnsmasq-fw.log` | `job=dnsmasq`, `student_id`, `host` |
| Dashboard container stdout | `job=otlab-dashboard`, `student_id`, `host` |
| Firewall container stdout | `job=otlab-firewall`, `student_id`, `host` |
| System journal | `job=syslog`, `student_id`, `host` |

`student_id` is the integer student # (1-20) from `/etc/otlab/student.env` on the student Pi.

## Default dashboards

Auto-provisioned from `grafana/dashboards/`:

| Dashboard | What |
|---|---|
| **OTLab — Classroom Overview** | Log volume per student (last 30 min); Suricata alert count per student; top signatures; recent firewall denies; per-student filter dropdown |

Add more by dropping JSON in `grafana/dashboards/` — Grafana picks them up every 30s.

## Alerting (future)

Loki ruler is enabled and reads `/loki/rules/` (mounted as `loki-data` volume). Rule format is Prometheus-style alerting rules over LogQL.

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
| Grafana dashboards, users, settings | `otlab-siem-grafana-data` Docker volume | `docker compose down -v` (custom dashboards in `grafana/dashboards/` re-provisioned automatically) |
| Promtail position file | `otlab-siem-promtail-data` Docker volume | safe to delete |

End-of-class wipe:

```
docker compose down -v
docker compose up -d
```

## Environment overrides

| Var | Default | Purpose |
|---|---|---|
| `GRAFANA_ADMIN_USER` | `admin` | Grafana admin login |
| `GRAFANA_ADMIN_PASS` | `P@ssw0rd!` | Grafana admin password — rotate per event |

## Why Loki and not Wazuh/Graylog

| Option | Why we didn't pick it |
|---|---|
| **Wazuh** | ~6 GB RAM minimum. Heavy install. Pedagogically richest but the teacher box would need a beefy laptop. Best choice if/when we move to dedicated server hardware. |
| **Graylog** | ~4 GB RAM. Solid but ICS-specific dashboards aren't OOTB. We'd build most of the value ourselves. |
| **Loki + Grafana + Promtail** *(chosen)* | ✅ ~2 GB RAM. Familiar (Grafana is widespread). Per-student labels make filtering trivial. Promtail runs on every student Pi with no measurable impact. |
| Custom rsyslog + SQLite | Cheapest but no exploration UI, no alerting beyond what we'd hand-build |

## Smoke test (after install)

1. `curl http://<teacher-ip>:3100/ready` → `ready`
2. `curl -u admin:P@ssw0rd! http://<teacher-ip>:3000/api/datasources` → Loki appears
3. Browse Grafana → Explore → query `{role="student"}` → logs appear within ~30s of student promtail starting
4. Browse "OTLab — Classroom Overview" dashboard → all 20 students show non-zero log rate

## See also

- **Classroom Installer & Reset** — how this fits into the install flow
- **Classroom Network Map** — where the SIEM sits in the network
- `teacher/agents/install-student-promtail.sh` (repo) — student-side installer
- [`teacher/siem/README.md`](https://github.com/aaroncorvo/otlab/blob/main/teacher/siem/README.md) — full repo doc
