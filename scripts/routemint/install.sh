#!/bin/sh
# Apply RouteMint cn-mainland/ads-light to the previously repaired HomeProxy.
set -eu
umask 077
HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
for cmd in uci ucode sing-box curl sha256sum; do
	command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done
[ -x /usr/sbin/proxy-switch ] || { echo "Install the HomeProxy repair first" >&2; exit 1; }
grep -q 'proxy-guard: migrate the 25.12.2 generator output' /etc/homeproxy/scripts/generate_client.uc || {
	echo "The sing-box 1.14 generator compatibility repair is required" >&2; exit 1;
}
for config in homeproxy passwall proxy-guard; do
	[ -z "$(uci -q changes "$config" 2>/dev/null)" ] || {
		echo "Pending $config edits: apply or revert them in LuCI first" >&2; exit 1;
	}
done
LOCK=/var/lock/routemint-homeproxy.lock
mkdir "$LOCK" 2>/dev/null || { echo "Another RouteMint import is running" >&2; exit 1; }
STAGE=""
BACKUP=""
CHANGED=0
SUCCESS=0
GUARD_STOPPED=0
PREV="$(uci -q get proxy-guard.main.active 2>/dev/null || true)"
cleanup() {
	rc=$?
	trap - EXIT INT TERM
	if [ "$CHANGED" -eq 1 ] && [ "$SUCCESS" -eq 0 ]; then
		echo "Activation failed; restoring original configs from $BACKUP" >&2
		for config in homeproxy passwall proxy-guard; do
			[ ! -f "$BACKUP/$config" ] || cp -p "$BACKUP/$config" "/etc/config/$config"
		done
		case "$PREV" in
			passwall|homeproxy|nikki|openclash|off) /usr/sbin/proxy-switch "$PREV" || true ;;
		esac
	fi
	if [ "$GUARD_STOPPED" -eq 1 ]; then /etc/init.d/proxy-guard start || true; fi
	case "$STAGE" in /tmp/routemint-*) rm -rf "$STAGE" ;; esac
	rmdir "$LOCK" 2>/dev/null || true
	exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
STAGE="$(mktemp -d /tmp/routemint-XXXXXX)"
mkdir -p "$STAGE/config" "$STAGE/delta" "$STAGE/rules"
BACKUP="/root/routemint-homeproxy-backup-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
for config in homeproxy passwall proxy-guard; do
	[ ! -f "/etc/config/$config" ] || cp -p "/etc/config/$config" "$BACKUP/$config"
done
echo "Backup: $BACKUP"
ORIGINAL="$(sha256sum /etc/config/homeproxy | awk '{print $1}')"
cp /etc/config/homeproxy "$STAGE/config/homeproxy"
export RM_STAGE="$STAGE" RM_PLAN="$HERE/cn-mainland.json"
ucode -c -o /dev/null "$HERE/configure.uc"
ucode "$HERE/configure.uc"
ucode -L /etc/homeproxy/scripts -c -o /dev/null "$STAGE/generate-preview.uc"

# Validate every published binary before changing live state. If direct GitHub
# access fails, use HomeProxy's existing local SOCKS listener as a fallback.
PORT="$(uci -q get homeproxy.infra.mixed_port 2>/dev/null || true)"
case "$PORT" in ''|*[!0-9]*) PORT=5330 ;; esac
while IFS= read -r category; do
	case "$category" in ''|*[!a-z0-9-]*) echo "Invalid category" >&2; exit 1 ;; esac
	url="https://raw.githubusercontent.com/zoran-sid/RouteMint-Rules/release/rules/$category/$category.srs"
	file="$STAGE/rules/$category.srs"
	echo "Checking rule set: $category"
	if ! curl -fsSL --connect-timeout 8 --max-time 20 "$url" -o "$file"; then
		curl -fsSL --socks5-hostname "127.0.0.1:$PORT" --connect-timeout 8 --max-time 60 "$url" -o "$file"
	fi
	sing-box rule-set decompile --output "$STAGE/check-rules.json" "$file" >/dev/null
done < "$STAGE/categories"

# Run the actual device generator against the isolated UCI directory and
# redirect only its JSON output. It does not overwrite the live runtime JSON.
ucode -L /etc/homeproxy/scripts -S "$STAGE/generate-preview.uc"
sing-box check -c "$STAGE/preview.json"
echo "Staged HomeProxy configuration passed sing-box check."
if [ "${1:-}" = "--check-only" ]; then
	echo "Check only: live configuration and services are unchanged."
	exit 0
fi
[ "$(sha256sum /etc/config/homeproxy | awk '{print $1}')" = "$ORIGINAL" ] || {
	echo "HomeProxy changed during preparation; import aborted" >&2; exit 1;
}
if pgrep -f '[p]roxy-guard-watchdog' >/dev/null; then
	/etc/init.d/proxy-guard stop
	GUARD_STOPPED=1
fi
cp "$STAGE/config/homeproxy" /etc/config/homeproxy.routemint-new
chmod 600 /etc/config/homeproxy.routemint-new
CHANGED=1
mv -f /etc/config/homeproxy.routemint-new /etc/config/homeproxy
uci set proxy-guard.main.state=normal
uci commit proxy-guard
/usr/sbin/proxy-switch homeproxy
attempt=0
stable=0
while [ "$attempt" -lt 60 ]; do
	if pgrep -f '[s]ing-box[[:space:]]+run[[:space:]].*sing-box-c\.json' >/dev/null; then
		stable=$((stable + 1))
		if [ "$stable" -ge 3 ]; then SUCCESS=1; break; fi
	else
		stable=0
	fi
	attempt=$((attempt + 1))
	sleep 1
done
[ "$SUCCESS" -eq 1 ] || { echo "HomeProxy core did not stay running; rolling back" >&2; exit 1; }
sync
echo "Applied RouteMint cn-mainland / ads-light. Remote rule sets update every 1d."
echo "Default traffic uses the existing node; AI/media reuse it unless overridden."
echo "Verify internet access and service exits. Backup: $BACKUP"
