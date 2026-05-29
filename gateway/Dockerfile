# OTLab — Modbus TCP gateway (IoT-to-OT translator)
# Small Python image, just pymodbus + httpx + the gateway script.
#
# Build (from repo root):
#   docker build -t otlab/modbus-gateway:latest -f gateway/Dockerfile gateway/

FROM python:3.13-slim-bookworm

# iproute2 — needed for the clab exec: hooks (`ip addr add ...`, `ip route add ...`)
# iputils-ping — handy for connectivity debugging from inside the container
# Slim image excludes these by default; both are tiny (~5 MB total).
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends iproute2 iputils-ping \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/gateway

# Pin dependency install; cache layer
COPY requirements.txt /opt/gateway/
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway.py /opt/gateway/

# Defaults — ESP32_HOST MUST be set by the topology env block.
ENV ESP32_HOST="" \
    MODBUS_LISTEN_PORT=502 \
    POLL_INTERVAL_S=1.0 \
    REST_TIMEOUT_S=2.0 \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

# Modbus TCP listen port
EXPOSE 502

CMD ["python3", "/opt/gateway/gateway.py"]
