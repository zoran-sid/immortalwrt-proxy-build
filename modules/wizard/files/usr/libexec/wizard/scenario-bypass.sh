#!/bin/sh
# 旁路由：主路由继续负责 DHCP，本机只做网关和 DNS
set -u
uci set network.lan.ipaddr="$WIZ_LAN_IP"
uci set network.lan.netmask="$WIZ_LAN_MASK"
uci set network.lan.gateway="$WIZ_GATEWAY"
uci set network.lan.delegate='0'

if [ -n "$WIZ_DNS1" ]; then
	uci set network.lan.dns="$WIZ_DNS1${WIZ_DNS2:+ $WIZ_DNS2}"
else
	uci set network.lan.dns="$WIZ_GATEWAY"
fi

# 不抢 DHCP，避免和主路由冲突
uci set dhcp.lan.ignore='1'
uci -q set dhcp.@dnsmasq[0].dns_forward_max='500' 2>/dev/null || true
exit 0
