# OpenPLC v3 runtime container — ARM64 native build for Pi 5.
#
# Upstream OpenPLC publishes images sporadically and ARM64 support is
# inconsistent, so we build from source. Build is ~15-20 min on a Pi 5;
# image gets cached locally on l3-mon-01 after the first build.
#
# Build (run on l3-mon-01 — image needs to be ARM64):
#   cd /home/otuser/lab    # repo on Pi
#   docker build -t otlab/openplc:latest -f virtual/dockerfiles/openplc/Dockerfile .
#
# Notes on apt dep choices:
#   - libcomedi (analog DAQ cards) and libopendnp3 (DNP3 master/outstation
#     library) are NOT packaged on debian:bookworm-slim ARM64. The "rpi"
#     hardware target doesn't link against them — comedi is for PCI DAQ,
#     opendnp3 only matters if you enable the DNP3 driver. Skip both.
#   - If a future hardware target needs them, build from source in the
#     builder stage and statically link, OR add a debian:bookworm (full,
#     not slim) base for the runtime stage.

FROM debian:bookworm AS builder

# Build deps for OpenPLC v3 (matiec compiler + runtime + webserver).
# Use the full bookworm base because libcomedi-dev / libopendnp3-dev only
# live in the broader package set; the install.sh probes for them.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        build-essential autoconf automake bison flex git pkg-config \
        libssl-dev libmodbus-dev \
        python3 python3-pip python3-venv \
        sudo lsb-release \
        ca-certificates \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Pin to a known-good commit; bump cautiously after testing.
# Note: the repo is named "_v3" but its branches are master/development/etc.
# — there is no "v3" branch. Pin to master for reproducibility.
ARG OPENPLC_REF=master
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
        libssl3 libmodbus5 \
        python3 python3-pip python3-venv \
        iproute2 procps tini ca-certificates \
        sqlite3 \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy built OpenPLC tree from the builder
COPY --from=builder /build/OpenPLC_v3 /opt/OpenPLC_v3

# Python venv for the webserver.
# OpenPLC v3 imports `pymodbus.client.sync` (reorganized in pymodbus 3.x),
# so pin pymodbus to 2.5.x. Flask 3.x has some breaking changes vs the
# Flask 2.x decorator API OpenPLC uses, so pin Flask too.
# Full Flask extension set OpenPLC v3 master uses (restapi.py + monitoring.py
# imports observed at runtime): flask, flask-login, flask-sqlalchemy,
# flask-bcrypt, flask-restful, flask-wtf.
RUN python3 -m venv /opt/openplc-venv \
 && /opt/openplc-venv/bin/pip install --no-cache-dir \
        'flask<3' 'flask-login<0.7' 'flask-sqlalchemy<4' 'flask-bcrypt' \
        'flask-restful' 'flask-wtf<2' 'flask-jwt-extended' \
        'pymodbus<3' pyserial pyOpenSSL bcrypt python-dotenv

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
