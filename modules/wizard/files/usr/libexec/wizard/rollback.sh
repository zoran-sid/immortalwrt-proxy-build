#!/bin/sh
# 恢复到最近一次向导备份（/etc/wizard-backups，持久化，重启后仍可用）。
# 只恢复 /etc/config，然后显式重载 network/dnsmasq/firewall 并恢复代理状态。
# 管理密码不在这里回滚：保持新密码可以避免用户被锁在设备外；这一点已在
# README 中写明。
set -u

BACKUP_DIR=/etc/wizard-backups
LOCK=/var/lock/wizard-rollback.lock
LATEST="$(ls -1t "$BACKUP_DIR"/wizard-backup-*.tar.gz 2>/dev/null | head -n1)"

if [ -z "$LATEST" ]; then
	logger -t wizard "rollback: no backup found"
	echo "rollback: no backup found" >&2
	exit 1
fi

if ! mkdir "$LOCK" 2>/dev/null; then
	logger -t wizard "rollback: another rollback is in progress"
	exit 3
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

logger -t wizard "rollback from $LATEST"
tar -xzf "$LATEST" -C / 2>/dev/null || {
	logger -t wizard "rollback: extract failed"
	exit 1
}

uci -q set wizard.main.applied='0'
uci -q set wizard.main.confirmed='0'
uci -q commit wizard 2>/dev/null || true

reload_config >/dev/null 2>&1 || true
if [ -x /etc/init.d/network ]; then /etc/init.d/network reload >/dev/null 2>&1 || true; fi
if [ -x /etc/init.d/dnsmasq ]; then /etc/init.d/dnsmasq restart >/dev/null 2>&1 || true; fi
if [ -x /etc/init.d/firewall ]; then /etc/init.d/firewall reload >/dev/null 2>&1 || true; fi
if [ -x /etc/init.d/proxy-guard ]; then /etc/init.d/proxy-guard restart >/dev/null 2>&1 || true; fi

# Bring the proxy frontend back to what the restored config says it should be.
ACTIVE="$(uci -q get proxy-guard.main.active 2>/dev/null)"
if [ -n "$ACTIVE" ] && [ "$ACTIVE" != "off" ]; then
	/usr/sbin/proxy-switch "$ACTIVE" >/dev/null 2>&1 || true
else
	/usr/sbin/proxy-switch off >/dev/null 2>&1 || true
fi

rm -f /var/run/wizard-rollback.pid
logger -t wizard "rollback done (from $LATEST)"
exit 0
