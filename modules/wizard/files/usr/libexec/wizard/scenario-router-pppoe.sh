#!/bin/sh
# 主路由，WAN 口 PPPoE 拨号
set -u
uci set network.lan.ipaddr="$WIZ_LAN_IP"
uci set network.lan.netmask="$WIZ_LAN_MASK"
uci -q delete network.lan.gateway 2>/dev/null || true
uci -q delete network.lan.dns 2>/dev/null || true

uci set network.wan.proto='pppoe'
uci set network.wan.username="$WIZ_WAN_USER"
uci set network.wan.password="$WIZ_WAN_PASS"
uci set network.wan.peerdns='0'

if [ -n "$WIZ_DNS1" ]; then
	uci set network.wan.dns="$WIZ_DNS1${WIZ_DNS2:+ $WIZ_DNS2}"
fi

uci set dhcp.lan.ignore='0'
uci set dhcp.lan.start='100'
uci set dhcp.lan.limit='150'
uci set dhcp.lan.leasetime='12h'
exit 0
