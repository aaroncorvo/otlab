# OTLab Dashboard — Flask + vanilla HTML/JS, packaged for the L3.5 DMZ.
#
# Same code as dashboard/dashboard.py — runs as `otuser` (uid 1000) inside
# the container. Mounts the audit-log + captures dirs as volumes so
# state persists across container restarts.
#
# Build:
#   docker build -t otlab/dashboard:latest -f virtual/dockerfiles/dashboard/Dockerfile .
# (run from repo root)
#
# In the virtualized lab, the dashboard's role narrows from "full SCADA"
# to "lab admin + curriculum surface" — Ignition handles SCADA in V2.
# The dashboard keeps: Test Library, Audit Log, Inject Fault, Cohort
# Reset, Modbus Write Playground, Topology, system health.

FROM python:3.13-slim-bookworm

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        iproute2 procps tini ca-certificates \
        openssh-client tcpdump \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/otlab

# Application code
COPY dashboard/                /opt/otlab/dashboard/

# Python deps
RUN pip install --no-cache-dir \
        flask flask-httpauth \
        pymodbus

# State directories — volumes attach here at runtime so audit log + pcaps
# survive container restarts.
RUN mkdir -p /var/lib/otlab/audit /var/lib/otlab/captures \
 && chmod 0750 /var/lib/otlab/audit /var/lib/otlab/captures

ENV PYTHONUNBUFFERED=1 \
    DASH_USER=otlab \
    DASH_PASS="P@ssw0rd!" \
    LISTEN_PORT=8000 \
    DASH_CERT=/opt/otlab/dashboard/cert.pem \
    DASH_KEY=/opt/otlab/dashboard/key.pem \
    AUDIT_DB=/var/lib/otlab/audit/audit.db \
    CAPTURES_DIR=/var/lib/otlab/captures

EXPOSE 8000

# Dashboard generates its own self-signed cert on first start if one
# doesn't exist (the cert-gen helper runs in dashboard.py initialization).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/otlab/dashboard/dashboard.py"]
