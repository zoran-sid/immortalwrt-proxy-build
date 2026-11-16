#!/bin/sh
# Keep node credentials and subscription sections intact.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
for cmd in curl sha256sum; do command -v "$cmd" >/dev/null; done
REV=4f50c01c033d03c30bf8811ca13b97706a89e630
BASE="https://raw.githubusercontent.com/zoran-sid/immortalwrt-proxy-build/$REV/modules/proxy-guard/files"
WORK=$(mktemp -d /tmp/proxy-ui-fix.XXXXXX)
INSTALLED=0
SUCCESS=0
cleanup() {
	if [ "$INSTALLED" = 1 ] && [ "$SUCCESS" != 1 ]; then
		cp -p "$BACKUP/reconcile" /usr/sbin/proxy-guard-reconcile
		cp -p "$BACKUP/init" /etc/init.d/proxy-guard
		cp -p "$BACKUP/switch" /usr/sbin/proxy-switch
		/etc/init.d/proxy-guard restart >/dev/null 2>&1 || true
		echo "Restored backup: $BACKUP" >&2
	fi
	rm -f "$WORK/reconcile" "$WORK/init" "$WORK/switch"
	rmdir "$WORK" 2>/dev/null || true
}
trap cleanup EXIT
curl -fSL --connect-timeout 15 --max-time 120 "$BASE/usr/sbin/proxy-guard-reconcile" -o "$WORK/reconcile"
curl -fSL --connect-timeout 15 --max-time 120 "$BASE/etc/init.d/proxy-guard" -o "$WORK/init"
curl -fSL --connect-timeout 15 --max-time 120 "$BASE/usr/sbin/proxy-switch" -o "$WORK/switch"
printf '%s  %s\n' b941aa066f3473c7e74fafcaf2d6b54883ce69bcdde14ab72c81df0ebdc8ac86 "$WORK/reconcile" | sha256sum -c -
printf '%s  %s\n' 7bfec03318ab526e28a23df03808732a992d374efd2326eb4bb4bf3f598b2328 "$WORK/init" | sha256sum -c -
printf '%s  %s\n' ffb38e4aa39f9a1be9c7863e1cdd015d50752f9a686a87eb24c5bdfb3ece66f2 "$WORK/switch" | sha256sum -c -
sh -n "$WORK/reconcile"
sh -n "$WORK/init"
sh -n "$WORK/switch"
BACKUP="/root/proxy-ui-fix-backup-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP"
cp -p /usr/sbin/proxy-guard-reconcile "$BACKUP/reconcile"
cp -p /etc/init.d/proxy-guard "$BACKUP/init"
cp -p /usr/sbin/proxy-switch "$BACKUP/switch"
INSTALLED=1
cp "$WORK/reconcile" /usr/sbin/proxy-guard-reconcile
cp "$WORK/init" /etc/init.d/proxy-guard
cp "$WORK/switch" /usr/sbin/proxy-switch
chmod 755 /usr/sbin/proxy-guard-reconcile /etc/init.d/proxy-guard /usr/sbin/proxy-switch
/etc/init.d/proxy-guard restart
SUCCESS=1
sync
echo "Installed native HomeProxy + modern PassWall lifecycle coordination. Backup: $BACKUP"
