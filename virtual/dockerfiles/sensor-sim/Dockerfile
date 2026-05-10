# sensor-sim — pure-stdlib Modbus TCP outstation, scenario-driven.
#
# Same code as plc/sensor-sim.py — built into a slim ARM64 image so it
# runs as a containerlab node on the Pi 5.
#
# Build:
#   docker build -t otlab/sensor-sim:latest -f virtual/dockerfiles/sensor-sim/Dockerfile .
# (run from repo root so plc/* is in the build context)

FROM python:3.13-slim-bookworm

# Minimal runtime — just iproute2 (containerlab's exec lines need `ip`)
# and procps for diagnostics.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends iproute2 procps tini \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/otlab

# Application code + scenarios. Pure stdlib — no pip install needed.
COPY plc/sensor-sim.py        /opt/otlab/sensor-sim.py
COPY plc/scenarios/*.json     /opt/otlab/scenarios/

ENV PYTHONUNBUFFERED=1 \
    SCENARIO=water-treatment \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=5020 \
    CTRL_PORT=5021 \
    SCENARIOS_DIR=/opt/otlab/scenarios

EXPOSE 5020 5021

# tini handles signal forwarding so `containerlab destroy` cleans up cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/otlab/sensor-sim.py"]
