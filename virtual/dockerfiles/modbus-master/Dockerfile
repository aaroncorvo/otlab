# modbus-master — purpose-built Modbus TCP master for the OTLab.
#
# Polls sensor-sim @ 10.20.30.70:5020 every 100 ms. Generates the
# canonical "legitimate master" traffic pattern on pcn-br0 that students
# see on the wire and that attack/detect scenarios are built around.
#
# Replaces OpenPLC for the master role in the virtual lab — OpenPLC's
# source build needs custom-patched libmodbus + libopendnp3 that aren't
# available on debian:bookworm-slim ARM64. The OpenPLC web UI still
# works on plc-1-virt / plc-2-virt for IEC 61131-3 click-around lessons;
# this container handles the actual on-the-wire polling.
#
# Build:
#   docker build -t otlab/modbus-master:latest -f virtual/dockerfiles/modbus-master/Dockerfile .

FROM python:3.13-slim-bookworm

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends iproute2 procps tini \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/otlab

COPY plc/modbus-master.py /opt/otlab/modbus-master.py

# pymodbus 3.x is fine for the master/client side (it's the server-side
# that's mid-migration in 3.x — see plc/sensor-sim.py for the full story
# on why we ship pure-stdlib Modbus on the outstation side).
RUN pip install --no-cache-dir 'pymodbus>=3'

ENV PYTHONUNBUFFERED=1 \
    SLAVE_IP=10.20.30.70 \
    SLAVE_PORT=5020 \
    POLL_PERIOD_S=0.1 \
    HR_COUNT=4 \
    COIL_COUNT=2 \
    LOG_INTERVAL_S=5.0

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/otlab/modbus-master.py"]
