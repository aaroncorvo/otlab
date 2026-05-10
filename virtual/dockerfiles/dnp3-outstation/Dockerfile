# dnp3-outstation — pure-stdlib DNP3 outstation, scenario-driven.
#
# Same code as plc/dnp3-outstation.py — built into a slim ARM64 image.
#
# Build:
#   docker build -t otlab/dnp3-outstation:latest -f virtual/dockerfiles/dnp3-outstation/Dockerfile .
# (run from repo root so plc/* is in the build context)

FROM python:3.13-slim-bookworm

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends iproute2 procps tini \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/otlab

COPY plc/dnp3-outstation.py   /opt/otlab/dnp3-outstation.py
COPY plc/scenarios/*.json     /opt/otlab/scenarios/

ENV PYTHONUNBUFFERED=1 \
    SCENARIO=water-treatment \
    LISTEN_PORT=20000 \
    OUTSTATION_ADDR=4 \
    MASTER_ADDR=3 \
    SCENARIOS_DIR=/opt/otlab/scenarios

EXPOSE 20000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/otlab/dnp3-outstation.py"]
