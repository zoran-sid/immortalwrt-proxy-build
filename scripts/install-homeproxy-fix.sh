#!/bin/sh
# Run from the unpacked repair bundle on the router, not on the build host.
set -eu
HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
[ -f "$HERE/rootfs/usr/sbin/homeproxy-config-patch" ] || exit 1
command -v ucode >/dev/null
command -v sing-box >/dev/null
BACKUP="/root/homeproxy-fix-backup-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
# Configs stay on the device and are never included in the distributed bundle.
for file in etc/config/homeproxy etc/config/passwall etc/config/proxy-guard \
    etc/homeproxy/scripts/generate_client.uc; do
    [ -f "/$file" ] || continue
    mkdir -p "$BACKUP/$(dirname "$file")"
    cp -p "/$file" "$BACKUP/$file"
done
find "$HERE/rootfs" -type f | while IFS= read -r source; do
    file="${source#"$HERE/rootfs/"}"
    [ -f "/$file" ] || continue
    mkdir -p "$BACKUP/$(dirname "$file")"
    cp -p "/$file" "$BACKUP/$file"
done
echo "Backup: $BACKUP"
# Install only the compatibility files first; validate before service changes.
mkdir -p /usr/share/homeproxy
cp "$HERE/rootfs/usr/share/homeproxy/compat-1.14.uc" /usr/share/homeproxy/
cp "$HERE/rootfs/usr/sbin/homeproxy-config-patch" /usr/sbin/
chmod 755 /usr/sbin/homeproxy-config-patch
/usr/sbin/homeproxy-config-patch
mkdir -p /var/run/homeproxy
chown sing-box:sing-box /var/run/homeproxy
if ! ucode -S /etc/homeproxy/scripts/generate_client.uc || \
   ! sing-box check -c /var/run/homeproxy/sing-box-c.json; then
    cp -p "$BACKUP/etc/homeproxy/scripts/generate_client.uc" /etc/homeproxy/scripts/
    echo "Config validation failed. Generator restored; services were not switched. Backup: $BACKUP" >&2
    exit 1
fi
# Files in this bundle are limited to the HomeProxy/proxy-guard repair.
cp -R "$HERE/rootfs/." /
chmod 755 /usr/sbin/homeproxy-config-patch /usr/sbin/proxy-switch \
    /usr/sbin/proxy-guard-reconcile /usr/sbin/proxy-guard-watchdog \
    /etc/init.d/proxy-guard /etc/hotplug.d/iface/99-proxy-guard
uci set proxy-guard.main.state=normal
uci commit proxy-guard
/usr/sbin/proxy-switch homeproxy
/etc/init.d/proxy-guard enable
/etc/init.d/proxy-guard restart
attempt=0
while [ "$attempt" -lt 10 ]; do
    if pgrep -f '[s]ing-box[[:space:]]+run[[:space:]].*sing-box-c\.json' >/dev/null; then
        sync
        echo "HomeProxy core is running; autostart restored. Backup: $BACKUP"
        echo "Verify internet access, then reboot and check the core again."
        exit 0
    fi
    attempt=$((attempt + 1))
    sleep 1
done
echo "Core is not running. Check /var/run/homeproxy/homeproxy.log. Backup: $BACKUP" >&2
exit 1
