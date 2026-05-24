# OTLab — MikroTik RouterOS config for a 20-student classroom.
#
# Tested on RouterOS 7.x (hAP ac²/³, RB5009, CCR2004). Should work on
# RouterOS 6.x with minor syntax tweaks (mostly /ip → /ip without
# subcommands).
#
# WHAT THIS CONFIGURES:
#   - LAN interface on bridge1 with 192.168.10.1/24
#   - DHCP server on the classroom segment
#   - 20 student DHCP reservations (placeholders — fill in MAC addresses)
#   - 1 teacher DHCP reservation
#   - 60 static routes (3 fabric layers × 20 students → student's classroom IP)
#   - Inter-student ACL block (defense-in-depth)
#   - NAT/MASQUERADE for outbound to WAN
#   - DNS forwarder
#
# PASTE THIS INTO THE MIKROTIK CLI in chunks, or upload as a .rsc file:
#     File → upload "20-student-classroom.rsc"
#     System → Scripts → run "20-student-classroom"
#
# BEFORE YOU PASTE:
#   1. Capture MAC addresses for all 21 Pis (1 teacher + 20 students)
#   2. Edit the "DHCP reservations" section below — replace each
#      AA:BB:CC:DD:EE:NN placeholder with the real MAC for that student
#   3. Confirm the LAN interface name (default `ether2` here; the hAP
#      uses ether2-master; the RB5009 uses ether2; adjust as needed)

# ════════════════════════════════════════════════════════════════════════
# 0. SAFETY — backup current config first (uncomment if rolling onto a
# live router; commented out by default so a fresh router doesn't fail)
# ════════════════════════════════════════════════════════════════════════
# /system backup save name=pre-otlab-classroom

# ════════════════════════════════════════════════════════════════════════
# 1. BRIDGE + INTERFACE — classroom LAN
# ════════════════════════════════════════════════════════════════════════
/interface bridge
add name=bridge-classroom comment="OTLab classroom LAN"

/interface bridge port
add bridge=bridge-classroom interface=ether2 comment="OTLab — student switch port"
add bridge=bridge-classroom interface=ether3 comment="OTLab — teacher port"
add bridge=bridge-classroom interface=ether4 comment="OTLab — spare/expansion"
add bridge=bridge-classroom interface=ether5 comment="OTLab — spare/expansion"

/ip address
add address=192.168.10.1/24 interface=bridge-classroom comment="OTLab classroom gateway"

# ════════════════════════════════════════════════════════════════════════
# 2. DHCP SERVER — classroom segment
# ════════════════════════════════════════════════════════════════════════
/ip pool
add name=otlab-pool ranges=192.168.10.200-192.168.10.250 comment="OTLab spillover (post-reservation)"

/ip dhcp-server
add address-pool=otlab-pool authoritative=yes disabled=no interface=bridge-classroom \
    lease-time=8h name=otlab-dhcp comment="OTLab classroom DHCP"

/ip dhcp-server network
add address=192.168.10.0/24 dns-server=192.168.10.1 gateway=192.168.10.1 \
    netmask=24 comment="OTLab classroom"

# ════════════════════════════════════════════════════════════════════════
# 3. DHCP RESERVATIONS — TEACHER (.10) + 20 STUDENTS (.101-.120)
#
# REPLACE the AA:BB:CC:DD:EE:NN placeholders with the real MACs for
# each Pi. Easiest way:
#   1. Plug in all Pis, let them get any DHCP lease
#   2. /ip dhcp-server lease print
#   3. Match hostnames (otlab-student-01 etc.) to MACs
#   4. Find/replace below
# ════════════════════════════════════════════════════════════════════════
/ip dhcp-server lease
# Teacher
add address=192.168.10.10  mac-address=AA:BB:CC:DD:EE:10 server=otlab-dhcp \
    comment="otlab-teacher"
# Students 1-20 → .101 - .120
add address=192.168.10.101 mac-address=AA:BB:CC:DD:EE:01 server=otlab-dhcp comment="otlab-student-01"
add address=192.168.10.102 mac-address=AA:BB:CC:DD:EE:02 server=otlab-dhcp comment="otlab-student-02"
add address=192.168.10.103 mac-address=AA:BB:CC:DD:EE:03 server=otlab-dhcp comment="otlab-student-03"
add address=192.168.10.104 mac-address=AA:BB:CC:DD:EE:04 server=otlab-dhcp comment="otlab-student-04"
add address=192.168.10.105 mac-address=AA:BB:CC:DD:EE:05 server=otlab-dhcp comment="otlab-student-05"
add address=192.168.10.106 mac-address=AA:BB:CC:DD:EE:06 server=otlab-dhcp comment="otlab-student-06"
add address=192.168.10.107 mac-address=AA:BB:CC:DD:EE:07 server=otlab-dhcp comment="otlab-student-07"
add address=192.168.10.108 mac-address=AA:BB:CC:DD:EE:08 server=otlab-dhcp comment="otlab-student-08"
add address=192.168.10.109 mac-address=AA:BB:CC:DD:EE:09 server=otlab-dhcp comment="otlab-student-09"
add address=192.168.10.110 mac-address=AA:BB:CC:DD:EE:0A server=otlab-dhcp comment="otlab-student-10"
add address=192.168.10.111 mac-address=AA:BB:CC:DD:EE:0B server=otlab-dhcp comment="otlab-student-11"
add address=192.168.10.112 mac-address=AA:BB:CC:DD:EE:0C server=otlab-dhcp comment="otlab-student-12"
add address=192.168.10.113 mac-address=AA:BB:CC:DD:EE:0D server=otlab-dhcp comment="otlab-student-13"
add address=192.168.10.114 mac-address=AA:BB:CC:DD:EE:0E server=otlab-dhcp comment="otlab-student-14"
add address=192.168.10.115 mac-address=AA:BB:CC:DD:EE:0F server=otlab-dhcp comment="otlab-student-15"
add address=192.168.10.116 mac-address=AA:BB:CC:DD:EE:11 server=otlab-dhcp comment="otlab-student-16"
add address=192.168.10.117 mac-address=AA:BB:CC:DD:EE:12 server=otlab-dhcp comment="otlab-student-17"
add address=192.168.10.118 mac-address=AA:BB:CC:DD:EE:13 server=otlab-dhcp comment="otlab-student-18"
add address=192.168.10.119 mac-address=AA:BB:CC:DD:EE:14 server=otlab-dhcp comment="otlab-student-19"
add address=192.168.10.120 mac-address=AA:BB:CC:DD:EE:15 server=otlab-dhcp comment="otlab-student-20"

# ════════════════════════════════════════════════════════════════════════
# 4. STATIC ROUTES — per-student fabric subnets via student's classroom IP
#
# 3 layers × 20 students = 60 routes. Lets the teacher SIEM and teacher
# admin panel reach every student's internal containers directly.
# ════════════════════════════════════════════════════════════════════════
/ip route
# Student 01 fabric
add dst-address=10.75.1.0/24  gateway=192.168.10.101 comment="student-01 DMZ"
add dst-address=10.30.1.0/24  gateway=192.168.10.101 comment="student-01 PCN"
add dst-address=10.50.1.0/24  gateway=192.168.10.101 comment="student-01 ENT"
# Student 02
add dst-address=10.75.2.0/24  gateway=192.168.10.102 comment="student-02 DMZ"
add dst-address=10.30.2.0/24  gateway=192.168.10.102 comment="student-02 PCN"
add dst-address=10.50.2.0/24  gateway=192.168.10.102 comment="student-02 ENT"
# Student 03
add dst-address=10.75.3.0/24  gateway=192.168.10.103 comment="student-03 DMZ"
add dst-address=10.30.3.0/24  gateway=192.168.10.103 comment="student-03 PCN"
add dst-address=10.50.3.0/24  gateway=192.168.10.103 comment="student-03 ENT"
# Student 04
add dst-address=10.75.4.0/24  gateway=192.168.10.104 comment="student-04 DMZ"
add dst-address=10.30.4.0/24  gateway=192.168.10.104 comment="student-04 PCN"
add dst-address=10.50.4.0/24  gateway=192.168.10.104 comment="student-04 ENT"
# Student 05
add dst-address=10.75.5.0/24  gateway=192.168.10.105 comment="student-05 DMZ"
add dst-address=10.30.5.0/24  gateway=192.168.10.105 comment="student-05 PCN"
add dst-address=10.50.5.0/24  gateway=192.168.10.105 comment="student-05 ENT"
# Student 06
add dst-address=10.75.6.0/24  gateway=192.168.10.106 comment="student-06 DMZ"
add dst-address=10.30.6.0/24  gateway=192.168.10.106 comment="student-06 PCN"
add dst-address=10.50.6.0/24  gateway=192.168.10.106 comment="student-06 ENT"
# Student 07
add dst-address=10.75.7.0/24  gateway=192.168.10.107 comment="student-07 DMZ"
add dst-address=10.30.7.0/24  gateway=192.168.10.107 comment="student-07 PCN"
add dst-address=10.50.7.0/24  gateway=192.168.10.107 comment="student-07 ENT"
# Student 08
add dst-address=10.75.8.0/24  gateway=192.168.10.108 comment="student-08 DMZ"
add dst-address=10.30.8.0/24  gateway=192.168.10.108 comment="student-08 PCN"
add dst-address=10.50.8.0/24  gateway=192.168.10.108 comment="student-08 ENT"
# Student 09
add dst-address=10.75.9.0/24  gateway=192.168.10.109 comment="student-09 DMZ"
add dst-address=10.30.9.0/24  gateway=192.168.10.109 comment="student-09 PCN"
add dst-address=10.50.9.0/24  gateway=192.168.10.109 comment="student-09 ENT"
# Student 10
add dst-address=10.75.10.0/24 gateway=192.168.10.110 comment="student-10 DMZ"
add dst-address=10.30.10.0/24 gateway=192.168.10.110 comment="student-10 PCN"
add dst-address=10.50.10.0/24 gateway=192.168.10.110 comment="student-10 ENT"
# Student 11
add dst-address=10.75.11.0/24 gateway=192.168.10.111 comment="student-11 DMZ"
add dst-address=10.30.11.0/24 gateway=192.168.10.111 comment="student-11 PCN"
add dst-address=10.50.11.0/24 gateway=192.168.10.111 comment="student-11 ENT"
# Student 12
add dst-address=10.75.12.0/24 gateway=192.168.10.112 comment="student-12 DMZ"
add dst-address=10.30.12.0/24 gateway=192.168.10.112 comment="student-12 PCN"
add dst-address=10.50.12.0/24 gateway=192.168.10.112 comment="student-12 ENT"
# Student 13
add dst-address=10.75.13.0/24 gateway=192.168.10.113 comment="student-13 DMZ"
add dst-address=10.30.13.0/24 gateway=192.168.10.113 comment="student-13 PCN"
add dst-address=10.50.13.0/24 gateway=192.168.10.113 comment="student-13 ENT"
# Student 14
add dst-address=10.75.14.0/24 gateway=192.168.10.114 comment="student-14 DMZ"
add dst-address=10.30.14.0/24 gateway=192.168.10.114 comment="student-14 PCN"
add dst-address=10.50.14.0/24 gateway=192.168.10.114 comment="student-14 ENT"
# Student 15
add dst-address=10.75.15.0/24 gateway=192.168.10.115 comment="student-15 DMZ"
add dst-address=10.30.15.0/24 gateway=192.168.10.115 comment="student-15 PCN"
add dst-address=10.50.15.0/24 gateway=192.168.10.115 comment="student-15 ENT"
# Student 16
add dst-address=10.75.16.0/24 gateway=192.168.10.116 comment="student-16 DMZ"
add dst-address=10.30.16.0/24 gateway=192.168.10.116 comment="student-16 PCN"
add dst-address=10.50.16.0/24 gateway=192.168.10.116 comment="student-16 ENT"
# Student 17
add dst-address=10.75.17.0/24 gateway=192.168.10.117 comment="student-17 DMZ"
add dst-address=10.30.17.0/24 gateway=192.168.10.117 comment="student-17 PCN"
add dst-address=10.50.17.0/24 gateway=192.168.10.117 comment="student-17 ENT"
# Student 18
add dst-address=10.75.18.0/24 gateway=192.168.10.118 comment="student-18 DMZ"
add dst-address=10.30.18.0/24 gateway=192.168.10.118 comment="student-18 PCN"
add dst-address=10.50.18.0/24 gateway=192.168.10.118 comment="student-18 ENT"
# Student 19
add dst-address=10.75.19.0/24 gateway=192.168.10.119 comment="student-19 DMZ"
add dst-address=10.30.19.0/24 gateway=192.168.10.119 comment="student-19 PCN"
add dst-address=10.50.19.0/24 gateway=192.168.10.119 comment="student-19 ENT"
# Student 20
add dst-address=10.75.20.0/24 gateway=192.168.10.120 comment="student-20 DMZ"
add dst-address=10.30.20.0/24 gateway=192.168.10.120 comment="student-20 PCN"
add dst-address=10.50.20.0/24 gateway=192.168.10.120 comment="student-20 ENT"

# ════════════════════════════════════════════════════════════════════════
# 5. FIREWALL — address lists, then filter rules
# ════════════════════════════════════════════════════════════════════════
/ip firewall address-list
# Classroom segment (all student + teacher classroom IPs)
add list=otlab-classroom address=192.168.10.0/24 comment="OTLab classroom segment"
# Teacher (allowed everywhere)
add list=otlab-teacher   address=192.168.10.10  comment="OTLab teacher host"
# Student classroom IPs (collectively)
add list=otlab-students  address=192.168.10.101-192.168.10.120 comment="OTLab student Pis (classroom)"
# All student fabric subnets summarized (lets us write one rule per layer)
add list=otlab-student-fabric address=10.75.0.0/16 comment="all student DMZ fabrics"
add list=otlab-student-fabric address=10.30.0.0/16 comment="all student PCN fabrics"
add list=otlab-student-fabric address=10.50.0.0/16 comment="all student ENT fabrics"

/ip firewall filter
# 1) Allow established/related anywhere
add chain=forward connection-state=established,related action=accept \
    comment="otlab: allow established"

# 2) Allow teacher → any student fabric
add chain=forward src-address-list=otlab-teacher \
    dst-address-list=otlab-student-fabric action=accept \
    comment="otlab: teacher → student fabric"

# 3) Allow students → teacher (for log shipping to SIEM)
add chain=forward src-address-list=otlab-students \
    dst-address=192.168.10.10 dst-port=3100 protocol=tcp action=accept \
    comment="otlab: student → teacher SIEM (Loki push)"

# 4) DENY student → student (classroom layer + fabric layer)
add chain=forward src-address-list=otlab-students \
    dst-address-list=otlab-students action=drop \
    comment="otlab: BLOCK student↔student (classroom)"
add chain=forward src-address-list=otlab-student-fabric \
    dst-address-list=otlab-student-fabric action=drop \
    comment="otlab: BLOCK student↔student (fabric)"

# 5) Allow students → internet (WAN egress)
add chain=forward src-address-list=otlab-students \
    out-interface-list=WAN action=accept \
    comment="otlab: students → internet"
add chain=forward src-address-list=otlab-student-fabric \
    out-interface-list=WAN action=accept \
    comment="otlab: student fabric → internet"

# ════════════════════════════════════════════════════════════════════════
# 6. NAT — masquerade outbound to WAN. Internal classroom routing is
# NOT NAT'd, so the teacher SIEM sees real source IPs from student
# fabric subnets.
# ════════════════════════════════════════════════════════════════════════
/interface list
add name=WAN comment="OTLab WAN interfaces"

/interface list member
add list=WAN interface=ether1 comment="OTLab WAN port (adjust to your uplink)"

/ip firewall nat
add chain=srcnat out-interface-list=WAN action=masquerade \
    comment="otlab: NAT to internet"

# ════════════════════════════════════════════════════════════════════════
# 7. DNS — router acts as forwarder for the classroom
# ════════════════════════════════════════════════════════════════════════
/ip dns
set allow-remote-requests=yes servers=1.1.1.1,8.8.8.8 cache-size=2048KiB

# ════════════════════════════════════════════════════════════════════════
# 8. SNMP (optional — lets the teacher panel query the MikroTik for
# interface counters). Leave commented unless you want it.
# ════════════════════════════════════════════════════════════════════════
# /snmp community
# add name=otlab-ro security=none addresses=192.168.10.10/32
# /snmp
# set enabled=yes

# ════════════════════════════════════════════════════════════════════════
# DONE. Verify with:
#   /ip dhcp-server lease print
#   /ip route print where dst-address~"10.30."
#   /ip firewall filter print where comment~"otlab"
#
# Teacher should now be able to:
#   - ping every 192.168.10.10X student IP
#   - curl http://10.30.5.40/  (student-05's dashboard, via classroom routing)
#   - see logs from all students in Grafana within ~1 min
#
# Student-to-student traffic should be REFUSED:
#   - student-03 trying to ping student-07 (192.168.10.107) → no reply
#   - student-03's fabric trying to reach 10.30.7.X → no reply
# ════════════════════════════════════════════════════════════════════════
