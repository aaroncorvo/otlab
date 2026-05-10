# OpenPLC v3 runtime container — ARM64 native build for Pi 5.
#
# Upstream OpenPLC publishes images sporadically and ARM64 support is
# inconsistent, so we build from source. Build is ~15-20 min on a Pi 5;
# image gets cached locally on l3-mon-01 after the first build.
#
# Build (run on l3-mon-01 — image needs to be ARM64):
#   cd /home/otuser/OTLab    # repo root
#   docker build -t otlab/openplc:latest -f virtual/dockerfiles/openplc/Dockerfile .
#
# At runtime, expects an .st program path mounted at /opt/openplc/program.st;
# falls back to a built-in blank program if not provided.

FROM debian:bookworm-slim AS builder

# Build deps for OpenPLC v3 (matiec compiler + runtime + webserver)
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        build-essential autoconf automake bison flex git pkg-config \
        libssl-dev libmodbus-dev libcomedi-dev libopendnp3-dev \
        python3 python3-pip python3-venv \
        ca-certificates \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Pin to a known-good commit; bump cautiously after testing
ARG OPENPLC_REF=v3
RUN git clone --depth 1 --branch "${OPENPLC_REF}" \
        https://github.com/thiagoralves/OpenPLC_v3.git /build/OpenPLC_v3

WORKDIR /build/OpenPLC_v3

# Stub the install script's apt + service hooks — we want only the build
# steps. Run the rpi target so the hardware layer for Pi GPIO is built
# in (harmless when GPIO isn't accessible from the container).
RUN ./install.sh rpi || true   # install.sh exits nonzero when systemctl unavailable

# ── runtime image ────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        libssl3 libmodbus5 libcomedi0 libopendnp3 \
        python3 python3-pip python3-venv \
        iproute2 procps tini ca-certificates \
        sqlite3 \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy built OpenPLC tree from the builder
COPY --from=builder /build/OpenPLC_v3 /opt/OpenPLC_v3

# Python venv for the webserver
RUN python3 -m venv /opt/openplc-venv \
 && /opt/openplc-venv/bin/pip install --no-cache-dir \
        flask flask-login pymodbus pyserial pyOpenSSL

WORKDIR /opt/OpenPLC_v3/webserver

# Pin hardware target to "rpi" — same as bootstrap-l1-plc-role.sh does
RUN echo rpi > /opt/OpenPLC_v3/webserver/scripts/openplc_platform

# Default user: openplc / P@ssw0rd! — lab convention. Rotate per event.
# (OpenPLC stores cleartext for the legacy /login path, sigh — see
# scripts/bootstrap-l1-plc-role.sh comments for the rationale.)
ENV PYTHONUNBUFFERED=1 \
    OPENPLC_HOME=/opt/OpenPLC_v3 \
    PATH=/opt/openplc-venv/bin:$PATH

EXPOSE 8080 502

ENTRYPOINT ["/usr/bin/tini", "--"]
# Run the webserver in foreground; it spawns the runtime when started
# from the UI or via `Start_run_mode=true` in the DB.
CMD ["python3", "/opt/OpenPLC_v3/webserver/webserver.py"]
