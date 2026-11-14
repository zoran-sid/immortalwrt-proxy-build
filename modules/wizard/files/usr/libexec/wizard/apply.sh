#!/bin/sh
# apply.sh — 向导的一次性应用入口（SSH 下也能直接用）。
#
#   /usr/libexec/wizard/apply.sh scenario=bypass lan_ip=192.168.1.2 \
#       lan_mask=255.255.255.0 gateway=192.168.1.1 proxy=homeproxy root_pass=xxxx
#
# 所有变更先写 uci 暂存区，最后统一 commit；失败时不做半套配置。
# 备份放在 /etc（持久化 overlay），重启后仍能回滚；/tmp 是 tmpfs，不能用于
# 90 秒确认窗口的备份。
set -u

SCENARIO=''
LAN_IP=''
LAN_MASK='255.255.255.0'
GATEWAY=''
DNS1=''
DNS2=''
WAN_USER=''
WAN_PASS=''
ROOT_PASS=''
PROXY='off'
SUB_URL=''
AGH='0'
FULLCONE='0'
TAILSCALE='0'
DDNS='0'

for arg in "$@"; do
	case "$arg" in
		scenario=*)  SCENARIO="${arg#*=}" ;;
		lan_ip=*)    LAN_IP="${arg#*=}" ;;
		lan_mask=*)  LAN_MASK="${arg#*=}" ;;
		gateway=*)   GATEWAY="${arg#*=}" ;;
		dns1=*)      DNS1="${arg#*=}" ;;
		dns2=*)      DNS2="${arg#*=}" ;;
		wan_user=*)  WAN_USER="${arg#*=}" ;;
		wan_pass=*)  WAN_PASS="${arg#*=}" ;;
		root_pass=*) ROOT_PASS="${arg#*=}" ;;
		proxy=*)     PROXY="${arg#*=}" ;;
		sub_url=*)   SUB_URL="${arg#*=}" ;;
		agh=*)       AGH="${arg#*=}" ;;
		fullcone=*)  FULLCONE="${arg#*=}" ;;
		tailscale=*) TAILSCALE="${arg#*=}" ;;
		ddns=*)      DDNS="${arg#*=}" ;;
		*) echo "unknown argument: $arg" >&2; exit 2 ;;
	esac
done

die() { echo "error: $*" >&2; logger -t wizard "apply failed: $*"; exit 1; }

valid_ipv4() {
	printf '%s' "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || return 1
	printf '%s' "$1" | awk -F. '{ for (i = 1; i <= 4; i++) if ($i + 0 > 255) exit 1 }'
}

case "$SCENARIO" in
	router-pppoe|router-dhcp|bypass|travel) ;;
	*) die "scenario must be one of router-pppoe, router-dhcp, bypass, travel" ;;
esac

valid_ipv4 "$LAN_IP" || die "invalid lan_ip: $LAN_IP"
valid_ipv4 "$LAN_MASK" || die "invalid lan_mask: $LAN_MASK"
[ "$LAN_MASK" = "255.255.255.0" ] || logger -t wizard "note: non-/24 lan_mask=$LAN_MASK"

if [ "$SCENARIO" = "bypass" ]; then
	valid_ipv4 "$GATEWAY" || die "bypass needs a valid upstream gateway"
	[ "$GATEWAY" != "$LAN_IP" ] || die "gateway must differ from lan_ip"
fi

if [ "$SCENARIO" = "router-pppoe" ]; then
	[ -n "$WAN_USER" ] || die "PPPoE scenario needs wan_user"
	[ -n "$WAN_PASS" ] || die "PPPoE scenario needs wan_pass"
fi

for ip in "$DNS1" "$DNS2"; do
	[ -n "$ip" ] || continue
	valid_ipv4 "$ip" || die "invalid DNS address: $ip"
done

if [ -n "$ROOT_PASS" ]; then
	[ "${#ROOT_PASS}" -ge 8 ] || die "root password must be at least 8 characters"
fi

case "$PROXY" in
	off|'') ;;
	passwall|homeproxy|nikki|openclash)
		[ -x "/etc/init.d/$PROXY" ] || die "proxy frontend $PROXY is not installed"
		;;
	*) die "unknown proxy frontend: $PROXY" ;;
esac

BACKUP_DIR=/etc/wizard-backups
APPLY_LOCK=/var/lock/wizard-apply.lock

if ! mkdir "$APPLY_LOCK" 2>/dev/null; then
	if [ -n "$(find "$APPLY_LOCK" -maxdepth 0 -mmin +5 2>/dev/null)" ]; then
		logger -t wizard "removing stale apply lock"
		rmdir "$APPLY_LOCK" 2>/dev/null
	fi
	if ! mkdir "$APPLY_LOCK" 2>/dev/null; then
		die "another wizard apply is in progress"
	fi
fi
trap 'rmdir "$APPLY_LOCK" 2>/dev/null' EXIT INT TERM

mkdir -p "$BACKUP_DIR" || die "cannot create $BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_DIR/wizard-backup-$TS.tar.gz"
tar -czf "$BACKUP" -C / etc/config 2>/dev/null || die "cannot back up /etc/config"
chmod 600 "$BACKUP" 2>/dev/null || true
logger -t wizard "backup written to $BACKUP"

# Keep the five most recent backups; this directory lives on flash.
ls -1t "$BACKUP_DIR"/wizard-backup-*.tar.gz 2>/dev/null | tail -n +6 | while IFS= read -r old; do
	rm -f "$old"
done

# 1) scenario-specific network/dhcp changes (no commit yet)
WIZ_SCENARIO="$SCENARIO" WIZ_LAN_IP="$LAN_IP" WIZ_LAN_MASK="$LAN_MASK" \
WIZ_GATEWAY="$GATEWAY" WIZ_DNS1="$DNS1" WIZ_DNS2="$DNS2" \
WIZ_WAN_USER="$WAN_USER" WIZ_WAN_PASS="$WAN_PASS" \
	sh "/usr/libexec/wizard/scenario-$SCENARIO.sh" || die "scenario script failed"

# 2) wizard state
uci -q get wizard.main >/dev/null 2>&1 || uci set wizard.main=wizard
uci set wizard.main.scenario="$SCENARIO"
uci set wizard.main.proxy="$PROXY"
uci set wizard.main.done='1'
uci set wizard.main.applied='1'
uci set wizard.main.confirmed='0'
uci -q delete wizard.main.sub_url 2>/dev/null || true
if [ -n "$SUB_URL" ]; then
	uci set wizard.main.sub_url="$SUB_URL"
fi

# 3) optional features, each guarded: missing app must not fail the wizard
if [ "$AGH" = "1" ] && [ -x /etc/init.d/adguardhome ]; then
	uci -q set adguardhome.@adguardhome[0].enabled='1' 2>/dev/null || true
	uci -q commit adguardhome 2>/dev/null || true
	/etc/init.d/adguardhome enable >/dev/null 2>&1 || true
	/etc/init.d/adguardhome restart >/dev/null 2>&1 || true
fi

if [ "$FULLCONE" = "1" ]; then
	uci -q set firewall.@defaults[0].fullcone='1' 2>/dev/null || true
	uci -q commit firewall 2>/dev/null || true
fi

if [ "$TAILSCALE" = "1" ] && [ -x /etc/init.d/tailscale ]; then
	uci -q set tailscale.settings.enabled='1' 2>/dev/null || true
	uci -q commit tailscale 2>/dev/null || true
	/etc/init.d/tailscale enable >/dev/null 2>&1 || true
	/etc/init.d/tailscale restart >/dev/null 2>&1 || true
fi

if [ "$DDNS" = "1" ] && [ -x /etc/init.d/ddns-go ]; then
	uci -q set ddns-go.config.enable='1' 2>/dev/null || true
	uci -q commit ddns-go 2>/dev/null || true
	/etc/init.d/ddns-go enable >/dev/null 2>&1 || true
	/etc/init.d/ddns-go restart >/dev/null 2>&1 || true
fi

# 4) commit everything touched by the scenarios
for c in network dhcp firewall wizard; do
	uci -q commit "$c" 2>/dev/null || true
done

# Apply the new network/DNS/firewall config for real. `reload_config` alone
# does not reliably re-apply a changed LAN address or restart dnsmasq.
reload_config >/dev/null 2>&1 || true
if [ -x /etc/init.d/network ]; then /etc/init.d/network reload >/dev/null 2>&1 || true; fi
if [ -x /etc/init.d/dnsmasq ]; then /etc/init.d/dnsmasq restart >/dev/null 2>&1 || true; fi
if [ -x /etc/init.d/firewall ]; then /etc/init.d/firewall reload >/dev/null 2>&1 || true; fi

# 5) proxy frontend: exactly one, through the guard. A failure leaves direct
# routing active; the wizard keeps working and surfaces a warning.
if [ "$PROXY" != "off" ] && [ "$PROXY" != "" ]; then
	if ! /usr/sbin/proxy-switch "$PROXY" >/dev/null 2>&1; then
		logger -t wizard "proxy-switch $PROXY failed, continuing with direct routing"
		echo "warning: proxy-switch $PROXY failed; direct routing left active"
	fi
fi

# 6) root password (last, so earlier failures never change the login)
if [ -n "$ROOT_PASS" ]; then
	if ! printf 'root:%s\n' "$ROOT_PASS" | chpasswd; then
		/usr/libexec/wizard/rollback.sh >/dev/null 2>&1 || true
		die "cannot set root password"
	fi
fi

# 7) 90 second confirmation window; only one guard at a time
GUARD_PID=/var/run/wizard-rollback.pid
if [ -s "$GUARD_PID" ]; then
	kill "$(cat "$GUARD_PID")" 2>/dev/null || true
fi
nohup /usr/libexec/wizard/rollback-guard.sh >/dev/null 2>&1 &
echo "$!" > "$GUARD_PID"
logger -t wizard "applied scenario=$SCENARIO proxy=$PROXY (confirmation pending)"
echo "applied"
