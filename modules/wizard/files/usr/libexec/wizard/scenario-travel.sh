#!/bin/sh
# 旅行/远程网关：WAN 走酒店的 DHCP，LAN 固定网段
set -u
uci set network.lan.ipaddr="$WIZ_LAN_IP"
uci set network.lan.netmask="$WIZ_LAN_MASK"
uci -q delete network.lan.gateway 2>/dev/null || true
uci -q delete network.lan.dns 2>/dev/null || true

uci set network.wan.proto='dhcp'

if [ -n "$WIZ_DNS1" ]; then
	uci set network.lan.dns="$WIZ_DNS1${WIZ_DNS2:+ $WIZ_DNS2}"
fi

uci set dhcp.lan.ignore='0'
uci set dhcp.lan.start='100'
uci set dhcp.lan.limit='100'
uci set dhcp.lan.leasetime='1h'
exit 0
