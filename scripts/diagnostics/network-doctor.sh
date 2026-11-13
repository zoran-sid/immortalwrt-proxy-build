#!/usr/bin/env bash
# network-doctor.sh — read-only diagnostics for an ImmortalWrt proxy router.
# Run ON the router. It never changes configuration, it only collects evidence
# and prints a short classification of the most likely failure class.
#
#   sh /root/network-doctor.sh                # full report, tarball in /tmp
#   sh /root/network-doctor.sh --redact-node-ip
#
# Secrets (subscription URLs, UUIDs, tokens, private keys) are stripped before
# anything is written to disk.
set -u

OUT=""
REDACT_NODE_IP=0
for arg in "$@"; do
  case "$arg" in
    --redact-node-ip) REDACT_NODE_IP=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

TS="$(date +%Y%m%d-%H%M%S)"
OUT="/tmp/network-doctor-$TS.log"

have() { command -v "$1" >/dev/null 2>&1; }
sec() { printf '\n===== %s =====\n' "$*"; }
run() { printf '\n$ %s\n' "$*"; "$@" 2>&1 || true; }

# Strip credentials that must never leave the device.
redact() {
  sed -E \
    -e 's#(vmess|vless|trojan|ss|ssr|hysteria2?|tuic)://[^ "]+#\1://<redacted>#g' \
    -e 's#(https?://)[^ /"]*(token|key|secret|password)=[^ &"]*#\1<redacted>#g' \
    -e 's#[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}#<uuid>#g' \
    -e 's#(auth[-_]?key|password|token|secret)([" =:]+)[^ ",}]+#\1\2<redacted>#gI'
}

if [ "$REDACT_NODE_IP" = "1" ]; then
  redact() { sed -E 's#([0-9]{1,3}\.){3}[0-9]{1,3}#<ip>#g'; }
fi

{
  sec SYSTEM
  run date
  run uptime
  run free
  run df -h
  run df -i
  run ubus call system board

  sec INTERFACES
  run ip -4 addr
  run ip -6 addr
  run ip route
  run ip -6 route
  run ip rule
  run ip -6 rule

  sec DNS
  printf '\n$ cat /etc/resolv.conf\n'; cat /etc/resolv.conf 2>&1 || true
  printf '\n$ /tmp/resolv.conf.d/resolv.conf.auto\n'; cat /tmp/resolv.conf.d/resolv.conf.auto 2>&1 || true
  if have nslookup; then
    for d in baidu.com google.com; do
      run nslookup "$d" 127.0.0.1
      run nslookup -type=AAAA "$d" 127.0.0.1
    done
  fi
  if have dig; then
    run dig +short A google.com @127.0.0.1
    run dig +short AAAA google.com @127.0.0.1
  fi

  sec SERVICES
  run ps w
  run ubus call service list
  if have ss; then run ss -lntup; else run netstat -lntup; fi
  run logread -l 200

  sec FIREWALL
  run nft list ruleset
  run ip rule show

  sec CONNECTIVITY
  run curl -4 -sS -o /dev/null -m 8 -w 'CN v4: %{http_code} %{time_total}s\n' https://www.baidu.com/
  run curl -6 -sS -o /dev/null -m 8 -w 'CN v6: %{http_code} %{time_total}s\n' https://www.baidu.com/
  run curl -4 -sS -o /dev/null -m 8 -w 'FOREIGN v4: %{http_code} %{time_total}s\n' https://www.google.com/
  run curl -6 -sS -o /dev/null -m 8 -w 'FOREIGN v6: %{http_code} %{time_total}s\n' https://www.google.com/

  sec PROXY CORES
  for p in xray sing-box mihomo clash openclash passwall; do
    pgrep -af "$p" 2>/dev/null || true
  done

  sec KERNEL
  run dmesg
  run grep -iE 'oom|segfault|panic' /var/log/messages

  sec CONNTRACK
  [ -r /proc/sys/net/netfilter/nf_conntrack_count ] && printf 'count=%s\n' "$(cat /proc/sys/net/netfilter/nf_conntrack_count)"
  [ -r /proc/sys/net/netfilter/nf_conntrack_max ] && printf 'max=%s\n' "$(cat /proc/sys/net/netfilter/nf_conntrack_max)"
} 2>&1 | redact > "$OUT"

# ---- classification: evidence based, never auto-remediation ----------------
classify() {
  local codes=""
  local body
  body="$(cat "$OUT")"

  grep -qiE 'Segmentation fault|panic|Call Trace' <<<"$body" && codes="$codes PROXY_CORE_CRASH_LOOP"
  grep -qiE 'Out of memory|oom-kill' <<<"$body" && codes="$codes OOM_EVENT"
  grep -qE '^cn=[0-9]+' <<<"$body" || true

  local dns_ok=0 proxy_ok=0
  grep -q 'FOREIGN v4: 200' <<<"$body" && proxy_ok=1
  grep -q 'CN v4: 200' <<<"$body" && dns_ok=1

  if [ "$dns_ok" = "1" ] && [ "$proxy_ok" = "0" ]; then
    codes="$codes DNS_UPSTREAM_FAILURE NODE_FAILURE"
  fi
  if [ "$dns_ok" = "0" ]; then
    codes="$codes DNS_LOCAL_FAILURE"
  fi
  if grep -qE 'FOREIGN v6: 000' <<<"$body" && grep -q 'FOREIGN v4: 200' <<<"$body"; then
    codes="$codes IPV6_PROXY_BYPASS"
  fi
  if ! grep -qE 'chain (mangle_prerouting|PSW|openclash|nikki)' <<<"$body"; then
    codes="$codes FIREWALL_RULE_MISSING"
  fi

  [ -z "$codes" ] && codes=" UNKNOWN"
  printf '\n===== CLASSIFICATION =====\n'
  for c in $codes; do
    case "$c" in
      DNS_LOCAL_FAILURE)    echo "DNS_LOCAL_FAILURE: LAN DNS listener not answering; check dnsmasq and proxy DNS port." ;;
      DNS_UPSTREAM_FAILURE) echo "DNS_UPSTREAM_FAILURE: names resolve but foreign traffic fails; test the proxy core directly." ;;
      NODE_FAILURE)         echo "NODE_FAILURE: proxy reachable in UI but egress fails; verify node/subscription, not only latency." ;;
      FIREWALL_RULE_MISSING) echo "FIREWALL_RULE_MISSING: no transparent-proxy chain found in nftables; rules never loaded." ;;
      IPV6_PROXY_BYPASS)    echo "IPV6_PROXY_BYPASS: IPv4 proxied, IPv6 not; AAAA and ::/0 routing/proxy path diverge." ;;
      OOM_EVENT)            echo "OOM_EVENT: proxy core was OOM-killed; check RAM, conntrack and restart policy." ;;
      PROXY_CORE_CRASH_LOOP) echo "PROXY_CORE_CRASH_LOOP: repeated core crashes; capture logread around the crash, check TUN leftovers." ;;
      UNKNOWN)              echo "UNKNOWN: no single dominant cause; inspect the raw report below." ;;
    esac
  done
}

classify
echo
echo "report: $OUT"
if have tar; then
  tar -czf "$OUT.tar.gz" -C /tmp "$(basename "$OUT")" 2>/dev/null && echo "bundle: $OUT.tar.gz"
fi
