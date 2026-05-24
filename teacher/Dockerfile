# Classroom Teacher Dashboard
#
# Build (from repo root):
#   docker build -t classroom-monitor -f teacher/Dockerfile teacher/
#
# Run:
#   docker run -d --name classroom-monitor \
#     -p 8080:8080 \
#     -e SCAN_BASE=192.168.1 \
#     -e SCAN_START=100 \
#     -e SCAN_END=200 \
#     -e SSH_USER=pi \
#     -e SSH_PASS=raspberry \
#     -e MAX_HOSTS=30 \
#     -v classroom-state:/var/lib/teacher \
#     classroom-monitor

FROM python:3.13-slim-bookworm

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        tini iputils-ping \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/teacher

# Application code (build context is teacher/)
COPY . /opt/teacher/

RUN pip install --no-cache-dir flask paramiko

# Persistent state volume
RUN mkdir -p /var/lib/teacher

ENV PYTHONUNBUFFERED=1 \
    LISTEN_PORT=8080 \
    SCAN_BASE=192.168.10 \
    SCAN_START=100 \
    SCAN_END=150 \
    SSH_USER=pi \
    SSH_PASS=raspberry \
    PROBE_INTERVAL=30 \
    HEALTH_INTERVAL=15 \
    MAX_HOSTS=0 \
    DATA_DIR=/var/lib/teacher

VOLUME ["/var/lib/teacher"]

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/teacher/teacher.py"]
