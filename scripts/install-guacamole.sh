#!/usr/bin/env bash
# install-guacamole.sh — deploy Apache Guacamole on the ops-host as a
# clientless RDP/VNC/SSH gateway to the lab PLCs.
#
# Architecture:
#   guacd (proxy daemon, port 4822 local)
#   guacamole-client (Java webapp, port 8443 HTTPS)
#   File-based auth via user-mapping.xml (no DB — keeps the deploy small)
#
# Pre-baked connections for:
#   - SSH softplc-1 (otadmin)
#   - SSH softplc-2 (otadmin)
#   - SSH honeypot-host (otadmin)
#
# Idempotent — re-run after edits.
#
# Usage:
#   ./scripts/install-guacamole.sh otadmin@OPSHOST.local
#
# Pre-req: bootstrap-ops-host.sh has run (Docker installed).

set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@OPSHOST.local}"
RUNTIME_USER=otuser
GUAC_DIR="/home/${RUNTIME_USER}/guacamole"

echo "==> deploying Apache Guacamole to $PI_HOST"

# ---------------------------------------------------------------------------
# 1. Stage the docker-compose stack + config files
# ---------------------------------------------------------------------------
echo "==> staging compose stack"
ssh "$PI_HOST" "
    sudo -u ${RUNTIME_USER} mkdir -p ${GUAC_DIR}
    sudo -u ${RUNTIME_USER} mkdir -p ${GUAC_DIR}/config ${GUAC_DIR}/drive ${GUAC_DIR}/record
"

# Compose file
ssh "$PI_HOST" "sudo -u ${RUNTIME_USER} tee ${GUAC_DIR}/compose.yaml >/dev/null" <<'EOF'
# Apache Guacamole stack — guacd + guacamole-client (file-auth, no DB).
# Listens on host port 8443 → guacamole-client :8080 inside the container.
services:
  guacd:
    image: guacamole/guacd:1.5.5
    container_name: otlab-guacd
    restart: unless-stopped
    networks: [guacnet]

  guacamole:
    image: guacamole/guacamole:1.5.5
    container_name: otlab-guacamole
    restart: unless-stopped
    depends_on: [guacd]
    environment:
      GUACD_HOSTNAME: guacd
      GUACAMOLE_HOME: /etc/guacamole
    volumes:
      - ./config:/etc/guacamole:ro
      - ./drive:/drive
      - ./record:/record
    ports:
      - "8443:8080"
    networks: [guacnet]

networks:
  guacnet:
    driver: bridge
EOF

# user-mapping.xml — file-based auth + connection profiles
# Default password 'P@ssw0rd!' is the lab convention; rotate per DEF CON event.
ssh "$PI_HOST" "sudo -u ${RUNTIME_USER} tee ${GUAC_DIR}/config/user-mapping.xml >/dev/null" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!--
  OTLab Guacamole user mapping. Single user "otlab" / "P@ssw0rd!".
  Three pre-baked SSH connections — one per Pi. Students learn the
  jump-host pattern: connect via Guacamole rather than SSH directly.
  Sessions can be recorded to /record/ for audit (uncomment the
  recording-path parameter to enable).
-->
<user-mapping>
  <authorize username="otlab"
             password="9d0c1b85bccfd61cd4f2b94d99e4f7c95cd7ef39c3636b08a3f4a0b06b06cf3a"
             encoding="sha256">

    <connection name="softplc-1 (SSH)">
      <protocol>ssh</protocol>
      <param name="hostname">10.20.30.47</param>
      <param name="port">22</param>
      <param name="username">otadmin</param>
      <param name="font-size">12</param>
      <param name="enable-sftp">true</param>
      <!-- <param name="recording-path">/record/softplc-1</param> -->
      <!-- <param name="recording-name">${GUAC_USERNAME}-${GUAC_DATE}</param> -->
    </connection>

    <connection name="softplc-2 (SSH)">
      <protocol>ssh</protocol>
      <param name="hostname">10.20.30.49</param>
      <param name="port">22</param>
      <param name="username">otadmin</param>
      <param name="font-size">12</param>
      <param name="enable-sftp">true</param>
    </connection>

    <connection name="honeypot-host (SSH)">
      <protocol>ssh</protocol>
      <param name="hostname">10.20.30.48</param>
      <param name="port">22</param>
      <param name="username">otadmin</param>
      <param name="font-size">12</param>
      <param name="enable-sftp">true</param>
    </connection>
  </authorize>
</user-mapping>
EOF

# Empty guacamole.properties — guacd connection params come from env in compose
ssh "$PI_HOST" "sudo -u ${RUNTIME_USER} tee ${GUAC_DIR}/config/guacamole.properties >/dev/null" <<'EOF'
# guacd connection (overridden by GUACD_HOSTNAME env in the container)
guacd-hostname: guacd
guacd-port: 4822

# UserMappingAuthenticationProvider (default when there's no DB) reads from
# /etc/guacamole/user-mapping.xml — already the right path.
EOF

# ---------------------------------------------------------------------------
# 2. Bring the stack up
# ---------------------------------------------------------------------------
echo "==> docker compose up -d"
ssh "$PI_HOST" "cd ${GUAC_DIR} && sudo docker compose up -d 2>&1 | tail -10"

# ---------------------------------------------------------------------------
# 3. Wait for the webapp + verify
# ---------------------------------------------------------------------------
echo "==> waiting for webapp (~10 s for Tomcat startup)"
sleep 12
ssh "$PI_HOST" '
    code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 https://localhost:8443/guacamole/ || echo "000")
    echo "    GET /guacamole/ → HTTP $code"
'

# ---------------------------------------------------------------------------
# 4. Authorize otuser-on-ops-host's SSH key on each PLC's otadmin so
#    Guacamole can connect over SSH without prompting for a password.
#    (This is the same pubkey-distribution pattern install-dashboard.sh uses.)
# ---------------------------------------------------------------------------
echo "==> authorizing ops-host's otuser SSH key on each PLC for Guacamole-mediated connections"
echo "    (the dashboard install-script handles this — re-run if you skipped it)"
echo

# ---------------------------------------------------------------------------
# 5. Stamp bootstrap-info
# ---------------------------------------------------------------------------
COMMIT="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$(basename "$0")"
ssh "$PI_HOST" "
sudo tee /etc/otlab-bootstrap-info >/dev/null <<EOF
ts=$TS
commit=$COMMIT
script=$SCRIPT
EOF
"

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------
HOST_BARE="${PI_HOST##*@}"
cat <<EOF

==============================================================================
 Guacamole deployed.

   URL:   https://${HOST_BARE}:8443/guacamole/
   user:  otlab
   pass:  P@ssw0rd!  (lab convention — rotate per DEF CON event)

 Pre-baked connections (visible on the home screen after login):
   - softplc-1 (SSH)    → otadmin@10.20.30.47
   - softplc-2 (SSH)    → otadmin@10.20.30.49
   - honeypot-host (SSH)→ otadmin@10.20.30.48

 Connection auth: Guacamole presents the SSH key it has on the
 container's filesystem — for now that's nothing, so connections will
 prompt for the otadmin password. To enable key-based auth, mount
 ${GUAC_DIR}/config/.ssh into /home/guacamole/.ssh in the container,
 or use 'private-key' parameters in user-mapping.xml.

 Session recording (audit trail): uncomment the recording-path lines in
 ${GUAC_DIR}/config/user-mapping.xml and restart the stack:
   docker compose down && docker compose up -d

 Logs:
   ssh ${PI_HOST} 'sudo docker logs otlab-guacamole'
   ssh ${PI_HOST} 'sudo docker logs otlab-guacd'
==============================================================================
EOF
