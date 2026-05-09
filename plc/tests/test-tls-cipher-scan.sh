#!/usr/bin/env bash
# test-tls-cipher-scan.sh — enumerate the TLS ciphers + cert exposed by
# the dashboard's :8000 HTTPS listener. Demonstrates self-signed cert
# detection + cipher inventory.
#
# Real-world lesson: students learn how a defender / attacker
# fingerprints HTTPS endpoints. The dashboard's cert is intentionally
# self-signed (no real CA in a teaching lab) — students should recognize
# this is a finding-of-note in a real engagement.

set -e
TARGET="${1:-10.20.30.49:8000}"

echo "=== nmap ssl-enum-ciphers @ ${TARGET} ==="
if ! command -v nmap >/dev/null; then
    echo "  nmap not installed. apt install nmap to enable this test." >&2
else
    nmap --script ssl-enum-ciphers -p ${TARGET##*:} ${TARGET%:*} 2>&1 | grep -E "TLSv|cipher|warnings|least strength|^\\|" | head -40
fi

echo
echo "=== openssl s_client cert dump ==="
echo "Q" | openssl s_client -connect "$TARGET" -showcerts 2>/dev/null | \
    openssl x509 -noout -subject -issuer -startdate -enddate -ext subjectAltName 2>&1 | head -20

echo
echo "=== heartbleed / weak-cipher checks ==="
if command -v testssl.sh >/dev/null; then
    testssl.sh --quiet --color 0 --severity HIGH "$TARGET" 2>&1 | head -30
else
    echo "  testssl.sh not installed (optional). For deeper scanning:"
    echo "    git clone https://github.com/drwetter/testssl.sh && ./testssl.sh $TARGET"
fi

echo
echo "Detection lesson:"
echo "  - Self-signed cert subject 'CN=otlab-dashboard' = an obvious finding"
echo "    in a real engagement (compare with corporate-CA-issued certs)"
echo "  - Cert SANs reveal infrastructure layout: tailscale IP + MagicDNS"
echo "    + lab-segment IP + mgmt IP all listed → architectural reconnaissance"
echo "  - Modern TLS configs reject TLS 1.0/1.1 + weak ciphers; check the"
echo "    nmap output for any flagged 'weak' lines"
