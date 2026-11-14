#!/usr/bin/env bash
# network-doctor.sh — read-only diagnostics for an ImmortalWrt proxy router.
# Run ON the router. It never changes configuration, it only collects evidence
# and prints a short classification of the most likely failure class.
#
#   sh /root/network-doctor.sh                # full report, tarball in /tmp
#   sh /root/network-doctor.sh --redact-node-ip
#
# Must stay BusyBox-ash compatible: no here-strings, no arrays, no [[ ]].
# Secrets (subscription URLs, UUIDs, tokens, private keys) are stripped before
# anything is written to disk, and --redact-node-ip ADDS IP masking instead of
# replacing the secret filter.
set -u

OUT=""
REDACT_NODE_IP=0
for arg in "$@"; do
  case "$arg" in
    --redact-node-ip) REDACT_NODE_IP=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

TS="$(date +%Y%m%d-%H%M%S)"
OUT="/tmp/network-doctor-$TS.log"

have() { command -v "$1" >/dev/null 2>&1; }
sec() { printf '\n===== %s =====\n' "$*"; }
run() { printf '\n$ %s\n' "$*"; "$@" 2>&1 || true; }

# Strip credentials that must never leave the device.
redact_secrets() {
  sed -E \
    -e 's#(vmess|vless|trojan|ss|ssr|hysteria2?|tuic|wireguard)://[^ "]+#\1://<redacted>#g' \
    -e 's#(https?://)[^ /"]*(token|key|secret|password|passwd|auth)=[^ &"]*#\1<redacted>#g' \
    -e 's#(https?://)[^ /"]*(/subscribe/|/sub/|/api/v1/client/subscribe)[^ "]*#\1<redacted>#g' \
    -e 's#[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}#<uuid>#g' \
    -e 's#(auth[-_]?key|password|token|secret|private[-_]?key)([" =:]+)[^ ",}]+#\1\2<redacted>#gI'
}

redact_node_ip() {
  sed -E 's#([0-9]{1,3}\.){3}[0-9]{1,3}#<ip>#g'
}

redact() {
  if [ "$REDACT_NODE_IP" = "1" ]; then
    redact_secrets | redact_node_ip
  else
    redact_secrets
  fi
}

# DNS probes are written as explicit markers so classification can tell a DNS
# failure from an egress failure instead of inferring it from "Google is down".
dns_marker() {
  domain="$1"
  if ! have nslookup && ! have dig; then
    echo "DNS $domain: unknown"
    return 0
  fi
  if have nslookup && nslookup "$domain" 127.0.0.1 >/dev/null 2>&1; then
    echo "DNS $domain: ok"
  elif have dig && [ -n "$(dig +short "$domain" @127.0.0.1 2>/dev/null)" ]; then
    echo "DNS $domain: ok"
  else
    echo "DNS $domain: fail"
  fi
}

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
  printf '\n# explicit DNS markers\n'
  dns_marker baidu.com
  dns_marker google.com
  if have nslookup; then
    run nslookup -type=AAAA google.com 127.0.0.1
  elif have dig; then
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
  printf '\n# proxy-guard state: %s\n' "$(uci -q get proxy-guard.main.active 2>/dev/null || echo unknown)"

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

  body_has() { printf '%s\n' "$body" | grep "$@"; }

  body_has -qiE 'Segmentation fault|panic|Call Trace' && codes="$codes PROXY_CORE_CRASH_LOOP"
  body_has -qiE 'Out of memory|oom-kill' && codes="$codes OOM_EVENT"

  local proxy_active=""
  proxy_active="$(uci -q get proxy-guard.main.active 2>/dev/null || true)"
  case "$proxy_active" in
    passwall|homeproxy|nikki|openclash) ;;
    *)
      # Fall back to a running core if the guard has no record (e.g. custom build).
      if pgrep -f 'xray|sing-box|mihomo|openclash' >/dev/null 2>&1; then
        proxy_active="detected"
      fi
      ;;
  esac

  local cn_ok=0 foreign_ok=0 dns_cn=0 dns_foreign=0
  body_has -q 'CN v4: 200' && cn_ok=1
  body_has -q 'FOREIGN v4: 200' && foreign_ok=1
  body_has -q 'DNS baidu.com: ok' && dns_cn=1
  body_has -q 'DNS google.com: ok' && dns_foreign=1

  if [ "$dns_cn" = "0" ] && body_has -q 'DNS baidu.com: fail'; then
    codes="$codes DNS_LOCAL_FAILURE"
  fi

  if [ -n "$proxy_active" ]; then
    # Names resolve, CN traffic works, foreign traffic does not -> the proxy
    # path or its node is broken. Split DNS from egress using google.com A.
    if [ "$cn_ok" = "1" ] && [ "$foreign_ok" = "0" ]; then
      if [ "$dns_foreign" = "1" ]; then
        codes="$codes NODE_FAILURE"
      else
        codes="$codes DNS_FOREIGN_FAILURE"
      fi
    fi
    if [ "$cn_ok" = "0" ]; then
      codes="$codes INTERNET_DOWN"
    fi
    if ! body_has -qE 'chain (mangle_prerouting|PSW|openclash|nikki|homeproxy|passwall)' \
       && ! body_has -qE 'table inet (passwall|homeproxy|nikki|openclash)'; then
      codes="$codes FIREWALL_RULE_MISSING"
    fi
    if body_has -q 'FOREIGN v6: 000' && [ "$foreign_ok" = "1" ]; then
      codes="$codes IPV6_PROXY_BYPASS"
    fi
  fi

  [ -z "$codes" ] && codes=" UNKNOWN"
  printf '\n===== CLASSIFICATION =====\n'
  for c in $codes; do
    case "$c" in
      DNS_LOCAL_FAILURE)    echo "DNS_LOCAL_FAILURE: LAN DNS listener does not resolve even CN names; check dnsmasq and the proxy DNS port." ;;
      DNS_FOREIGN_FAILURE)  echo "DNS_FOREIGN_FAILURE: CN names resolve but foreign names do not; the proxy DNS path/policy is broken (not necessarily the node)." ;;
      NODE_FAILURE)         echo "NODE_FAILURE: names resolve (incl. foreign) but foreign HTTP fails; verify the node/subscription egress." ;;
      FIREWALL_RULE_MISSING) echo "FIREWALL_RULE_MISSING: a proxy frontend is active but no transparent-proxy chain/table exists; rules never loaded." ;;
      IPV6_PROXY_BYPASS)    echo "IPV6_PROXY_BYPASS: IPv4 proxied, IPv6 not; AAAA and ::/0 routing/proxy path diverge." ;;
      INTERNET_DOWN)        echo "INTERNET_DOWN: even CN IPv4 fails; check WAN/LAN first, do not blame the proxy." ;;
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
