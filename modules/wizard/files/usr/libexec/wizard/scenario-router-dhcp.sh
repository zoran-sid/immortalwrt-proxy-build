#!/bin/sh
# 主路由，WAN 从上游 DHCP 获取地址
set -u
uci set network.lan.ipaddr="$WIZ_LAN_IP"
uci set network.lan.netmask="$WIZ_LAN_MASK"
uci -q delete network.lan.gateway 2>/dev/null || true
uci -q delete network.lan.dns 2>/dev/null || true

uci set network.wan.proto='dhcp'

if [ -n "$WIZ_DNS1" ]; then
	uci set network.wan.peerdns='0'
	uci set network.wan.dns="$WIZ_DNS1${WIZ_DNS2:+ $WIZ_DNS2}"
fi

uci set dhcp.lan.ignore='0'
uci set dhcp.lan.start='100'
uci set dhcp.lan.limit='150'
uci set dhcp.lan.leasetime='12h'
exit 0
