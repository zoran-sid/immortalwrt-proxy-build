#!/bin/sh
# Targeted runtime repair: no node, subscription, firewall or network edits.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
for cmd in curl sha256sum; do command -v "$cmd" >/dev/null; done
REV=0c50b200cc6a2d288e82de3457c02ca9118fa657
BASE="https://raw.githubusercontent.com/zoran-sid/immortalwrt-proxy-build/$REV/modules/proxy-guard/files"
WORK=$(mktemp -d /tmp/proxy-ui-fix.XXXXXX)
INSTALLED=0
SUCCESS=0
cleanup() {
	if [ "$INSTALLED" = 1 ] && [ "$SUCCESS" != 1 ]; then
		cp -p "$BACKUP/reconcile" /usr/sbin/proxy-guard-reconcile
		cp -p "$BACKUP/init" /etc/init.d/proxy-guard
		/etc/init.d/proxy-guard restart >/dev/null 2>&1 || true
		echo "Restored backup: $BACKUP" >&2
	fi
	rm -f "$WORK/reconcile" "$WORK/init"
	rmdir "$WORK" 2>/dev/null || true
}
trap cleanup EXIT
curl -fSL --connect-timeout 15 --max-time 120 "$BASE/usr/sbin/proxy-guard-reconcile" -o "$WORK/reconcile"
curl -fSL --connect-timeout 15 --max-time 120 "$BASE/etc/init.d/proxy-guard" -o "$WORK/init"
printf '%s  %s\n' d44c05de769d8cc3d101f33b9d2ab8a9c6264ccf7c790db44ad9d433a46b6cb2 "$WORK/reconcile" | sha256sum -c -
printf '%s  %s\n' e28fe1ac447c3bcb7a4a163199cecadcd2a4cc20da8ee3aa3eeccba8be235445 "$WORK/init" | sha256sum -c -
sh -n "$WORK/reconcile"
sh -n "$WORK/init"
BACKUP="/root/proxy-ui-fix-backup-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP"
cp -p /usr/sbin/proxy-guard-reconcile "$BACKUP/reconcile"
cp -p /etc/init.d/proxy-guard "$BACKUP/init"
INSTALLED=1
cp "$WORK/reconcile" /usr/sbin/proxy-guard-reconcile
cp "$WORK/init" /etc/init.d/proxy-guard
chmod 755 /usr/sbin/proxy-guard-reconcile /etc/init.d/proxy-guard
/etc/init.d/proxy-guard restart
SUCCESS=1
sync
echo "Installed UI switch + master-off coordination repair. Backup: $BACKUP"
