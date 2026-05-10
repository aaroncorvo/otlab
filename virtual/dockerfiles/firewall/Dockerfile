# DMZ ↔ PCN firewall — alpine + iptables + a startup script that lays
# down the canonical Purdue-conduit policy.
#
# This is the L3.5 ↔ L1/L2 enforcement point per the architecture
# diagram. Every packet between dmz-br0 and pcn-br0 traverses this
# container, where the policy lives.
#
# Build:
#   docker build -t otlab/firewall:latest -f virtual/dockerfiles/firewall/Dockerfile virtual/dockerfiles/firewall

FROM alpine:3.20

RUN apk add --no-cache \
        iptables ip6tables iproute2 conntrack-tools tini bash curl ca-certificates \
        dnsmasq \
 && rm -rf /var/cache/apk/*

# Policy + helper scripts
COPY start-firewall.sh     /opt/otlab-firewall/start-firewall.sh
COPY fw-state-exporter.sh  /opt/otlab-firewall/fw-state-exporter.sh
COPY policy.iptables       /opt/otlab-firewall/policy.iptables

RUN chmod 0755 /opt/otlab-firewall/start-firewall.sh \
               /opt/otlab-firewall/fw-state-exporter.sh

ENV PCN_NET=10.20.30.0/24 \
    DMZ_NET=192.168.75.0/24 \
    PCN_IF=eth2 \
    DMZ_IF=eth1 \
    UPLINK_IF=eth0 \
    DNS_UPSTREAM_1=1.1.1.1 \
    DNS_UPSTREAM_2=8.8.8.8

# Keep the container alive — we want a long-running process so containerlab
# treats it as healthy. tail -f /dev/null is the standard idiom for "I'm
# a netns container, the network plumbing is the product, not a process."
#
# Note: start-firewall.sh ALSO launches dnsmasq as a DNS forwarder bound
# to the firewall's bridge-facing IPs (.1 on each zone). It's invoked as
# the LAST exec hook in topology.clab.yaml so the IPs already exist by
# then. Running dnsmasq from CMD instead would race the clab IP assignment
# and crash with "unknown interface".
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["tail", "-f", "/dev/null"]
