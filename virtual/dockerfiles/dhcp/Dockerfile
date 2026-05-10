# dhcp — alpine + dnsmasq, parameterized via env so the same image runs
# as dhcp-dmz, dhcp-pcn, or any future per-zone DHCP server.
#
# One container per zone:
#   - hands out IPs in a configurable scope
#   - advertises the firewall container as the gateway + DNS
#   - logs leases to /var/log/dnsmasq.log
#   - statically assigns reserved IPs (e.g. modbus-master .43, l1-plc-01 .47)
#     via DHCP-host entries — keeps known IPs sticky even if a static config
#     gets clobbered
#
# Build:
#   docker build -t otlab/dhcp:latest -f virtual/dockerfiles/dhcp/Dockerfile virtual/dockerfiles/dhcp

FROM alpine:3.20

RUN apk add --no-cache \
        dnsmasq iproute2 procps tini bash \
        ca-certificates \
 && rm -rf /var/cache/apk/*

# Entrypoint substitutes env vars into a dnsmasq config and execs dnsmasq.
COPY entrypoint.sh /opt/otlab-dhcp/entrypoint.sh
RUN chmod 0755 /opt/otlab-dhcp/entrypoint.sh

ENV LISTEN_IF=eth1 \
    NETWORK=192.168.75.0 \
    NETMASK=255.255.255.0 \
    PREFIX=24 \
    SELF_IP=192.168.75.2 \
    GATEWAY=192.168.75.1 \
    DNS_SERVER=192.168.75.1 \
    RANGE_START=192.168.75.150 \
    RANGE_END=192.168.75.199 \
    LEASE_TIME=12h \
    DOMAIN=otlab.local \
    LOG_DHCP=1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/opt/otlab-dhcp/entrypoint.sh"]
