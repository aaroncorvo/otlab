# Classroom Teacher Dashboard
#
# Build (from repo root):
#   docker build -t otlab-teacher -f teacher/Dockerfile teacher/
#
# Run (single-Pi standalone classroom, no FortiGate):
#   docker run -d --name otlab-teacher \
#     -p 8080:8080 \
#     -e SCAN_BASE=192.168.1 \
#     -e SCAN_START=100 \
#     -e SCAN_END=200 \
#     -e MAX_HOSTS=30 \
#     -v classroom-state:/var/lib/teacher \
#     otlab-teacher
#
# Run (with FortiGate port monitor):
#   docker run -d --name otlab-teacher \
#     -p 8080:8080 \
#     -e SCAN_BASE=192.168.1 \
#     -e FORTI_IP=192.168.0.10 \
#     -v classroom-state:/var/lib/teacher \
#     otlab-teacher

FROM python:3.13-slim-bookworm

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        tini iputils-ping \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/teacher

# Application code (build context is teacher/)
COPY . /opt/teacher/

# Python deps: Flask for the HTTP layer, paramiko for SSH to student Pis,
# flask-httpauth for basic auth on the dashboard.
RUN pip install --no-cache-dir flask flask-httpauth paramiko

# Persistent state volume
RUN mkdir -p /var/lib/teacher

# Defaults match the OTLab convention (otadmin / P@ssw0rd!). Override
# at `docker run` for different cohort credential schemes.
ENV PYTHONUNBUFFERED=1 \
    LISTEN_PORT=8080 \
    SCAN_BASE=192.168.1 \
    SCAN_START=100 \
    SCAN_END=200 \
    SSH_USER=otadmin \
    SSH_PASS="P@ssw0rd!" \
    DASH_USER=otlab \
    DASH_PASS="P@ssw0rd!" \
    FORTI_IP= \
    FORTI_TIMEOUT=8 \
    PROBE_INTERVAL=30 \
    HEALTH_INTERVAL=15 \
    MAX_HOSTS=0 \
    DATA_DIR=/var/lib/teacher

VOLUME ["/var/lib/teacher"]

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/teacher/teacher.py"]
