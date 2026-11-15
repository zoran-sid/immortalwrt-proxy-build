#!/bin/sh
set -eu
HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
[ -x /usr/sbin/proxy-guard-reconcile ] || { echo "Install the base repair first" >&2; exit 1; }
BACKUP="/root/homeproxy-fast-start-backup-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
for file in etc/init.d/proxy-guard usr/sbin/homeproxy-boot-retry usr/sbin/proxy-switch; do
	if [ -f "/$file" ]; then
		mkdir -p "$BACKUP/$(dirname "$file")"
		cp -p "/$file" "$BACKUP/$file"
	fi
	sh -n "$HERE/rootfs/$file"
done
cp "$HERE/rootfs/usr/sbin/homeproxy-boot-retry" /usr/sbin/
cp "$HERE/rootfs/etc/init.d/proxy-guard" /etc/init.d/
cp "$HERE/rootfs/usr/sbin/proxy-switch" /usr/sbin/
chmod 755 /usr/sbin/homeproxy-boot-retry /etc/init.d/proxy-guard /usr/sbin/proxy-switch
/etc/init.d/proxy-guard enable
/etc/init.d/proxy-guard restart
sync
echo "HomeProxy boot retry: every 5 seconds, up to 2 minutes. Backup: $BACKUP"
echo "Saved frontend: $(uci -q get proxy-guard.main.active)"
