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
        iputils-ping \
        openssh-client tcpdump openssl \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Probes use /bin/ping. Debian's iputils-ping ships with file caps already
# (cap_net_raw+ep), so unprivileged users can open ICMP sockets — but
# `setcap` here is belt-and-suspenders in case the upstream package ever
# changes (some minimal images strip caps).
RUN setcap cap_net_raw+ep /bin/ping || true

WORKDIR /opt/otlab

# Application code
COPY dashboard/                /opt/otlab/dashboard/

# Python deps
RUN pip install --no-cache-dir \
        flask flask-httpauth \
        pymodbus

# Generate a self-signed TLS cert at build time, SAN-rich so the dashboard
# is reachable via every meaningful name + IP. 10-year validity (lab cert,
# rotate per DEF CON event if you want — just rebuild the image).
# In V2/V3 we can swap this for an Authentik-managed cert if desired.
RUN openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /opt/otlab/dashboard/key.pem \
        -out    /opt/otlab/dashboard/cert.pem \
        -days   3650 \
        -subj   '/CN=otlab-dashboard/O=Maple Ridge Treatment Plant/OU=ICS Village' \
        -addext 'subjectAltName=DNS:l3-mon-01,DNS:l3-mon-01.local,DNS:dashboard,DNS:localhost,IP:127.0.0.1,IP:192.168.75.40,IP:10.20.30.49,IP:100.77.255.56' \
        2>/dev/null \
 && chmod 600 /opt/otlab/dashboard/key.pem \
 && chmod 644 /opt/otlab/dashboard/cert.pem

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
