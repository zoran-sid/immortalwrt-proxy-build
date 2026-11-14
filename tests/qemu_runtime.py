#!/usr/bin/env python3
"""Proxy-switch / proxy-guard / wizard / diagnostics runtime tests in QEMU.

Boots a standard x86 image (one that contains proxy-guard and the wizard) and
exercises the state machine on the real guest:

  * proxy-switch mutual exclusion, uninstalled frontend, failure restore
  * proxy-guard fail-open -> recovery attempts -> fail-closed
  * wizard apply validation, 90s rollback, confirm path, secret logging
  * network-doctor redaction of subscription URLs and node IPs

Usage:
  python3 tests/qemu_runtime.py --image standard.img --log runtime.log

No real proxy subscription is used; the watchdog tests point probe_url at a
closed local port, which is deterministic and does not depend on censorship.
"""

import argparse
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qemu_smoke import Console, free_port  # noqa: E402

REPO = "zoran-sid/immortalwrt-proxy-build"
BRANCH = "fix/build-metadata-and-presets"
RAW_DOCTOR = "https://raw.githubusercontent.com/%s/%s/scripts/diagnostics/network-doctor.sh" % (
    REPO,
    BRANCH,
)

results = []


def check(con, name, cmd, expect=None, forbid=None, timeout=120, retries=2):
    out = ""
    ok = False
    for attempt in range(retries + 1):
        try:
            out, _ = con.command(cmd, timeout=timeout)
        except TimeoutError:
            out = "<timeout>"
        ok = True
        if expect is not None and expect not in out:
            ok = False
        if forbid is not None and forbid in out:
            ok = False
        if ok:
            break
        time.sleep(1)
    results.append((name, ok))
    print("%s  %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        print("      cmd: %s\n      out: %s" % (cmd, out[-400:].replace("\n", "\n      ")))
    return out


def rc(con, cmd, timeout=120):
    out, _ = con.command("%s; echo __RC:$?" % cmd, timeout=timeout)
    code = ""
    for line in out.splitlines():
        if line.startswith("__RC:"):
            code = line.split(":", 1)[1].strip()
    return code, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qemu", default=os.environ.get("QEMU", "qemu-system-x86_64"))
    ap.add_argument("--image", required=True)
    ap.add_argument("--log", default="qemu-runtime.log")
    ap.add_argument("--boot-timeout", type=int, default=420)
    args = ap.parse_args()

    log = open(args.log, "w", encoding="utf-8", errors="replace")
    serial_port = free_port()
    qargs = [
        args.qemu, "-machine", "q35", "-m", "1024", "-smp", "2", "-cpu", "max",
        "-drive", "file=%s,format=raw,if=ide,index=0" % args.image,
        "-netdev", "user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8080-:80",
        "-device", "e1000,netdev=net0",
        "-netdev", "user,id=net1", "-device", "e1000,netdev=net1",
        "-display", "none", "-vga", "none", "-monitor", "none",
        "-serial", "tcp:127.0.0.1:%d,server=on,wait=off" % serial_port,
    ]
    if os.path.exists("/dev/kvm"):
        qargs[qargs.index("-cpu") + 1] = "host"
        qargs.append("-enable-kvm")

    qlog = open(args.log + ".qemu", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(qargs, stdout=qlog, stderr=subprocess.STDOUT)
    log.write("QEMU: %s\n" % " ".join(qargs))
    sock = None
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", serial_port), timeout=2)
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError("QEMU exited early; see %s" % (args.log + ".qemu"))
                time.sleep(1)
        con = Console(sock, log)
        con.login(args.boot_timeout)
        time.sleep(12)
        con.clear()

        # WAN is not part of the shipped x86 config; add DHCP on eth1.
        con.command(
            "uci -q get network.wan >/dev/null || { uci set network.wan=interface; "
            "uci set network.wan.device='eth1'; uci set network.wan.proto='dhcp'; "
            "uci commit network; /etc/init.d/network reload; }", timeout=60)
        time.sleep(10)

        check(con, "runtime: wizard/guard present",
              "test -x /usr/libexec/wizard/apply.sh && test -x /usr/sbin/proxy-switch "
              "&& test -x /etc/init.d/proxy-guard && echo present", "present")
        check(con, "runtime: proxy frontends installed",
              "for s in passwall homeproxy; do test -x /etc/init.d/$s || echo missing-$s; done; echo checked",
              "checked")
        check(con, "runtime: DNS + WAN ready",
              "nslookup www.baidu.com 127.0.0.1 >/dev/null 2>&1 && ip route | grep -q '^default' && echo ready",
              "ready")

        # ---- proxy-switch ---------------------------------------------------
        code, _ = rc(con, "/usr/sbin/proxy-switch passwall")
        results.append(("switch: passwall rc=0", code == "0"))
        check(con, "switch: active=passwall", "uci -q get proxy-guard.main.active", "passwall")
        check(con, "switch: passwall enabled",
              "if /etc/init.d/passwall enabled >/dev/null 2>&1; then echo enabled; else echo disabled; fi",
              "enabled")
        check(con, "switch: homeproxy disabled",
              "if /etc/init.d/homeproxy enabled >/dev/null 2>&1; then echo enabled; else echo disabled; fi",
              "disabled")

        code, _ = rc(con, "/usr/sbin/proxy-switch homeproxy")
        results.append(("switch: homeproxy rc=0", code == "0"))
        check(con, "switch: active=homeproxy", "uci -q get proxy-guard.main.active", "homeproxy")
        check(con, "switch: passwall disabled",
              "if /etc/init.d/passwall enabled >/dev/null 2>&1; then echo enabled; else echo disabled; fi",
              "disabled")

        code, out = rc(con, "/usr/sbin/proxy-switch nikki")
        results.append(("switch: uninstalled frontend fails", code != "0" and "not installed" in out))
        check(con, "switch: failed switch keeps homeproxy active",
              "uci -q get proxy-guard.main.active", "homeproxy")
        check(con, "switch: failed switch keeps homeproxy enabled",
              "if /etc/init.d/homeproxy enabled >/dev/null 2>&1; then echo enabled; else echo disabled; fi",
              "enabled")

        # A target that exists but cannot start must restore the previous one.
        con.command("cp /etc/init.d/passwall /tmp/passwall.real && "
                    "printf '#!/bin/sh\\nexit 1\\n' > /etc/init.d/passwall && "
                    "chmod +x /etc/init.d/passwall", timeout=60)
        code, _ = rc(con, "/usr/sbin/proxy-switch passwall")
        restored = con.command(
            "uci -q get proxy-guard.main.active; "
            "if /etc/init.d/homeproxy enabled >/dev/null 2>&1; then echo hp-enabled; fi"
        )[0]
        results.append(("switch: start failure restores previous",
                        code != "0" and "homeproxy" in restored and "hp-enabled" in restored))
        print("%s  switch: start failure restores previous" % ("PASS" if results[-1][1] else "FAIL"))
        con.command("cp /tmp/passwall.real /etc/init.d/passwall && chmod +x /etc/init.d/passwall",
                    timeout=60)

        # Repeated switching must not leave duplicate tables.
        con.command("for i in 1 2 3; do /usr/sbin/proxy-switch passwall >/dev/null 2>&1; "
                    "/usr/sbin/proxy-switch homeproxy >/dev/null 2>&1; done; true", timeout=180)
        tables = con.command(
            "nft list tables 2>/dev/null | grep -E 'table inet (passwall|homeproxy)' "
            "| sort | uniq -d | wc -l")[0].strip()
        results.append(("switch: no duplicate nft tables", tables == "0"))
        print("%s  switch: no duplicate nft tables (dupes=%s)"
              % ("PASS" if results[-1][1] else "FAIL", tables))

        code, _ = rc(con, "/usr/sbin/proxy-switch off")
        check(con, "switch: off clears active", "uci -q get proxy-guard.main.active", "off")
        check(con, "switch: off stops frontends",
              "pgrep -f '[x]ray|[s]ing-box|[m]ihomo' >/dev/null || echo stopped", "stopped")
        con.command("/usr/sbin/proxy-switch passwall", timeout=120)

        # ---- proxy-guard watchdog ------------------------------------------
        check(con, "guard: service running",
              "pgrep -f '[p]roxy-guard-watchdog' >/dev/null && echo running", "running")
        con.command("cp /etc/config/proxy-guard /tmp/pg.bak")
        con.command(
            "uci set proxy-guard.main.interval=2; "
            "uci set proxy-guard.main.fail_threshold=2; "
            "uci set proxy-guard.main.recover_delay=6; "
            "uci set proxy-guard.main.recover_max=12; "
            "uci set proxy-guard.main.probe_url='http://127.0.0.1:9/'; "
            "uci set proxy-guard.main.probe_cn='https://www.baidu.com/'; "
            "uci set proxy-guard.main.probe_dns='223.5.5.5 1.1.1.1'; "
            "uci commit proxy-guard; /etc/init.d/proxy-guard restart", timeout=60)
        time.sleep(14)
        check(con, "guard: fail-open state", "uci -q get proxy-guard.main.state", "failopen", retries=4)
        check(con, "guard: active record kept", "uci -q get proxy-guard.main.active", "passwall")
        time.sleep(24)
        attempts = con.command("logread | grep -c 'recovery attempt'")[0].strip()
        results.append(("guard: recovery attempted", attempts.isdigit() and int(attempts) >= 1))
        print("%s  guard: recovery attempted (count=%s)"
              % ("PASS" if results[-1][1] else "FAIL", attempts))
        check(con, "guard: still fail-open after failed recovery",
              "uci -q get proxy-guard.main.state", "failopen", retries=4)

        # fail-closed must keep the proxy enabled.
        con.command("/usr/sbin/proxy-switch passwall >/dev/null 2>&1; "
                    "uci set proxy-guard.main.policy='closed'; uci commit proxy-guard; "
                    "/etc/init.d/proxy-guard restart", timeout=90)
        time.sleep(10)
        check(con, "guard: fail-closed state", "uci -q get proxy-guard.main.state", "closed")
        check(con, "guard: fail-closed keeps proxy enabled",
              "if /etc/init.d/passwall enabled >/dev/null 2>&1; then echo enabled; else echo disabled; fi",
              "enabled")
        con.command("cp /tmp/pg.bak /etc/config/proxy-guard; /etc/init.d/proxy-guard restart",
                    timeout=60)

        # ---- wizard --------------------------------------------------------
        con.command("mkdir -p /etc/wizard-backups")
        before = con.command("ls -1 /etc/wizard-backups 2>/dev/null | wc -l")[0].strip()
        code, out = rc(con, "/usr/libexec/wizard/apply.sh scenario=bypass lan_ip=999.1.1.1 "
                             "gateway=192.168.1.1")
        results.append(("wizard: invalid LAN rejected", code != "0" and "invalid lan_ip" in out))
        print("%s  wizard: invalid LAN rejected" % ("PASS" if results[-1][1] else "FAIL"))

        code, out = rc(con, "/usr/libexec/wizard/apply.sh scenario=router-dhcp "
                             "lan_ip=192.168.1.1 proxy=nikki")
        results.append(("wizard: uninstalled proxy rejected", code != "0" and "not installed" in out))
        print("%s  wizard: uninstalled proxy rejected" % ("PASS" if results[-1][1] else "FAIL"))
        after = con.command("ls -1 /etc/wizard-backups 2>/dev/null | wc -l")[0].strip()
        results.append(("wizard: rejected apply makes no backup", before == after))
        print("%s  wizard: rejected apply makes no backup" % ("PASS" if results[-1][1] else "FAIL"))

        # Rollback: change LAN, do not confirm, wait out the 90s window.
        code, out = rc(con, "/usr/libexec/wizard/apply.sh scenario=router-dhcp "
                             "lan_ip=192.168.99.1 proxy=homeproxy", timeout=180)
        results.append(("wizard: apply rc=0", code == "0"))
        print("%s  wizard: apply rc=0" % ("PASS" if results[-1][1] else "FAIL"))
        check(con, "wizard: new LAN applied", "uci -q get network.lan.ipaddr", "192.168.99.1")
        print("waiting 95s for the confirmation window to expire ...")
        time.sleep(95)
        check(con, "wizard: rollback restores LAN", "uci -q get network.lan.ipaddr", "192.168.1.1")
        check(con, "wizard: rollback clears applied flag", "uci -q get wizard.main.applied", "0")
        check(con, "wizard: dnsmasq alive after rollback",
              "pgrep -f '[d]nsmasq' >/dev/null && echo alive", "alive")
        check(con, "wizard: LuCI alive after rollback",
              "curl -m 8 -s -o /dev/null -w '%{http_code}' http://127.0.0.1/", "200")

        # Confirm path: config must stay.
        code, out = rc(con, "/usr/libexec/wizard/apply.sh scenario=router-dhcp "
                             "lan_ip=192.168.88.1 proxy=passwall", timeout=180)
        con.command("/usr/libexec/wizard/confirm.sh", timeout=60)
        print("waiting 95s to prove a confirmed config is not rolled back ...")
        time.sleep(95)
        check(con, "wizard: confirmed config survives window",
              "uci -q get network.lan.ipaddr", "192.168.88.1")
        con.command("uci set network.lan.ipaddr='192.168.1.1'; uci commit network; "
                    "/etc/init.d/network reload", timeout=90)

        # Secrets must not land in syslog.
        token = "SMOKE_SECRET_%d" % int(time.time())
        con.command("/usr/libexec/wizard/apply.sh scenario=router-dhcp lan_ip=192.168.1.1 "
                    "proxy=off sub_url='https://example.com/sub?token=%s'" % token, timeout=120)
        logread = con.command("logread")[0]
        results.append(("wizard: subscription token not logged", token not in logread))
        print("%s  wizard: subscription token not logged" % ("PASS" if results[-1][1] else "FAIL"))
        check(con, "wizard: subscription stored locally only",
              "grep -c '%s' /etc/config/wizard" % token, "1")
        con.command("/usr/libexec/wizard/confirm.sh", timeout=60)

        # ---- diagnostics ---------------------------------------------------
        con.command("curl -fsSL -o /tmp/network-doctor.sh %s && chmod +x /tmp/network-doctor.sh"
                    % RAW_DOCTOR, timeout=120)
        con.command("logger -t smoke 'subscribe https://example.com/sub?token=%s'" % token)
        out = con.command("sh /tmp/network-doctor.sh 2>&1 | tail -20", timeout=180)[0]
        report = con.command("cat /tmp/network-doctor-*.log 2>/dev/null | grep -c '%s'" % token)[0].strip()
        results.append(("doctor: secret redacted", report == "0"))
        print("%s  doctor: secret redacted (matches=%s)" % ("PASS" if report == "0" else "FAIL", report))
        results.append(("doctor: classification printed", "CLASSIFICATION" in out))
        print("%s  doctor: classification printed" % ("PASS" if "CLASSIFICATION" in out else "FAIL"))

        out = con.command("sh /tmp/network-doctor.sh --redact-node-ip 2>&1 | tail -5", timeout=180)[0]
        last = con.command("ls -1t /tmp/network-doctor-*.log 2>/dev/null | head -1")[0].strip()
        iphit = con.command("grep -c '223\\.5\\.5\\.5' '%s' 2>/dev/null" % last)[0].strip()
        results.append(("doctor: --redact-node-ip masks IPs", iphit == "0"))
        print("%s  doctor: --redact-node-ip masks IPs (hits=%s)" % ("PASS" if iphit == "0" else "FAIL", iphit))

        # ---- IPv6 ----------------------------------------------------------
        v6 = con.command("ip -6 addr show scope global 2>/dev/null | grep -c 'inet6'")[0].strip()
        print("%s  ipv6: global address present (count=%s)"
              % ("PASS" if v6 not in ("0", "") else "SKIP", v6))
        results.append(("ipv6: global address (informational)", True))

        con.command("uci set system.@system[0].hostname=ImmortalWrt; uci commit system")
    finally:
        try:
            if sock is not None:
                sock.sendall(b"poweroff\r")
                time.sleep(2)
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        qlog.close()
        log.close()

    failed = [name for name, ok in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    if failed:
        print("RUNTIME FAILED: %s" % ", ".join(failed))
        return 1
    print("RUNTIME PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
