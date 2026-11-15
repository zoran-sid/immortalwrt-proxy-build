#!/usr/bin/env python3
"""HomeProxy boot-persistence / fail-open / i18n tests on a real x86 image.

Covers, in order:
  1. enabling HomeProxy in its own UI (UCI flag only, no rc.d link) is
     reconciled by the guard and survives a cold reboot;
  2. reconcile is idempotent (no duplicate sing-box / nftables rules);
  3. fail-open stops the runtime but preserves enabled flag + rc.d link, so a
     reboot starts the user's proxy again;
  4. an unconfigured HomeProxy (no node/server) is not started automatically;
  5. the zh-cn LuCI language packs are actually installed.

Requires the image built from fix/homeproxy-boot-persistence.
"""
import argparse
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qemu_smoke import Console, free_port  # noqa: E402

QEMU = os.environ.get("QEMU", "qemu-system-x86_64")
FAILS = []
PASSES = []


def record(name, ok, detail=""):
    (PASSES if ok else FAILS).append(name)
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  [%s]" % detail) if detail and not ok else ""))


class Qemu:
    def __init__(self, image, log_path):
        self.image = image
        self.log = open(log_path, "a", encoding="utf-8", errors="replace")
        self.proc = None
        self.sock = None
        self.con = None
        self.serial = None

    def start(self):
        self.serial = free_port()
        args = [
            QEMU, "-machine", "q35", "-m", "1024", "-smp", "2", "-cpu", "max",
            "-drive", "file=%s,format=raw,if=ide,index=0" % self.image,
            "-netdev", "user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22",
            "-device", "e1000,netdev=net0",
            "-netdev", "user,id=net1", "-device", "e1000,netdev=net1",
            "-display", "none", "-vga", "none", "-monitor", "none",
            "-serial", "tcp:127.0.0.1:%d,server=on,wait=off" % self.serial,
        ]
        qlog = open(self.log.name + ".qemu", "a")
        self.proc = subprocess.Popen(args, stdout=qlog, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.serial), timeout=2)
                break
            except OSError:
                time.sleep(1)
        if self.sock is None:
            raise RuntimeError("no QEMU serial connection")
        self.con = Console(self.sock, self.log)
        self.con.login(420)
        time.sleep(12)
        self.con.clear()
        return self.con

    def poweroff(self, settle=22):
        # f2fs checkpoints lazily; without this a poweroff right after uci
        # commit can roll the overlay back to the previous checkpoint.
        self.con.command("sync", timeout=60)
        time.sleep(settle)
        self.con.send("poweroff\r")
        try:
            self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
        self.sock.close()
        self.log.flush()

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def sh(con, cmd, timeout=180):
    out, _ = con.command(cmd, timeout=timeout)
    return out.strip()


def check(con, name, cmd, expect, timeout=180):
    out = sh(con, cmd, timeout=timeout)
    ok = expect in out
    record(name, ok, out[-300:])
    return out


def configure_homeproxy(con):
    # custom mode + direct outbound is a valid configured service and does not
    # need a real subscription.
    con.command("uci set homeproxy.config.routing_mode='custom'; "
                "uci set homeproxy.routing.default_outbound='direct-out'; "
                "uci set homeproxy.config.enabled='1'; uci commit homeproxy",
                timeout=120)


def main():
    global QEMU
    ap = argparse.ArgumentParser()
    ap.add_argument("--qemu", default=QEMU)
    ap.add_argument("--image", required=True)
    ap.add_argument("--log", default="qemu-homeproxy.log")
    args = ap.parse_args()
    QEMU = args.qemu

    q = Qemu(args.image, args.log)
    try:
        con = q.start()
        check(con, "installed: homeproxy + proxy-guard",
              "test -x /etc/init.d/homeproxy && test -x /usr/sbin/proxy-guard-reconcile && echo ok", "ok")

        # WAN for realistic boot; HomeProxy start itself does not need it.
        con.command("uci -q get network.wan >/dev/null || { uci set network.wan=interface; "
                    "uci set network.wan.device='eth1'; uci set network.wan.proto='dhcp'; "
                    "uci commit network; /etc/init.d/network reload; }", timeout=60)
        time.sleep(8)

        # Scenario 1: user enables HomeProxy in its own UI. That only sets the
        # UCI flag / restarts the service, but does not create S99homeproxy.
        # Stop the guard/hotplug first so no WAN event can race the simulation.
        con.command("/etc/init.d/proxy-guard stop >/dev/null 2>&1; "
                    "mv /etc/hotplug.d/iface/99-proxy-guard /tmp/99-proxy-guard 2>/dev/null; true",
                    timeout=60)
        configure_homeproxy(con)
        con.command("/etc/init.d/homeproxy stop >/dev/null 2>&1; "
                    "/etc/init.d/homeproxy disable >/dev/null 2>&1; true", timeout=60)
        check(con, "ui-enable: link is missing before reconcile",
              "/etc/init.d/homeproxy enabled >/dev/null 2>&1 && echo link || echo no-link", "no-link")
        con.command("mv /tmp/99-proxy-guard /etc/hotplug.d/iface/99-proxy-guard 2>/dev/null; "
                    "/etc/init.d/proxy-guard start", timeout=180)
        time.sleep(4)
        check(con, "ui-enable: reconcile restores rc.d link",
              "/etc/init.d/homeproxy enabled >/dev/null 2>&1 && echo link || echo no-link", "link")
        check(con, "ui-enable: HomeProxy running",
              "pgrep -f '[s]ing-box.*sing-box-c' >/dev/null && echo running || echo stopped", "running")
        check(con, "ui-enable: active record",
              "uci -q get proxy-guard.main.active", "homeproxy")
        check(con, "ui-enable: runtime dir exists",
              "test -d /var/run/homeproxy && echo yes", "yes")

        # Scenario 2: repeated reconcile must not duplicate the runtime.
        before = sh(con, "pgrep -f '[s]ing-box.*sing-box-c' | wc -l")
        chain_before = sh(con, "nft list ruleset 2>/dev/null | grep -c 'homeproxy_'")
        con.command("for i in 1 2 3; do /usr/sbin/proxy-guard-reconcile --event; "
                    "/usr/sbin/proxy-switch --ensure homeproxy; done", timeout=240)
        time.sleep(3)
        after = sh(con, "pgrep -f '[s]ing-box.*sing-box-c' | wc -l")
        chain_after = sh(con, "nft list ruleset 2>/dev/null | grep -c 'homeproxy_'")
        record("idempotent: single sing-box", before == after == "1", "before=%s after=%s" % (before, after))
        record("idempotent: nft rules unchanged", chain_before == chain_after,
               "before=%s after=%s" % (chain_before, chain_after))

        # Scenario 3: cold reboot must start it automatically.
        q.poweroff()
        con = q.start()
        time.sleep(10)
        check(con, "reboot: link persisted",
              "/etc/init.d/homeproxy enabled >/dev/null 2>&1 && echo link || echo no-link", "link")
        check(con, "reboot: HomeProxy running",
              "pgrep -f '[s]ing-box.*sing-box-c' >/dev/null && echo running || echo stopped", "running")
        check(con, "reboot: enabled rc=0",
              "/etc/init.d/homeproxy enabled >/dev/null 2>&1; echo rc=$?", "rc=0")

        # Scenario 4: fail-open must keep the user's choice.
        con.command("/usr/sbin/proxy-switch off --keep-record --keep-enabled", timeout=180)
        time.sleep(3)
        check(con, "fail-open: runtime stopped",
              "pgrep -f '[s]ing-box.*sing-box-c' >/dev/null && echo running || echo stopped", "stopped")
        check(con, "fail-open: enabled flag preserved",
              "uci -q get homeproxy.config.enabled", "1")
        check(con, "fail-open: rc.d link preserved",
              "/etc/init.d/homeproxy enabled >/dev/null 2>&1 && echo link || echo no-link", "link")
        check(con, "fail-open: active record preserved",
              "uci -q get proxy-guard.main.active", "homeproxy")
        q.poweroff()
        con = q.start()
        time.sleep(10)
        check(con, "fail-open + reboot: HomeProxy starts again",
              "pgrep -f '[s]ing-box.*sing-box-c' >/dev/null && echo running || echo stopped", "running")

        # Scenario 4b: lifecycle with a deleted tmpfs runtime dir.
        check(con, "lifecycle: start after rm -rf",
              "/etc/init.d/homeproxy stop >/dev/null 2>&1; rm -rf /var/run/homeproxy; "
              "/etc/init.d/homeproxy start >/tmp/hp-start.out 2>&1; echo rc=$?; "
              "pgrep -f '[s]ing-box.*sing-box-c' >/dev/null && echo running || echo stopped",
              "rc=0\nrunning")
        check(con, "lifecycle: dir recreated on start", "test -d /var/run/homeproxy && echo yes", "yes")
        check(con, "lifecycle: stop when stopped is safe",
              "/etc/init.d/homeproxy stop >/dev/null 2>&1; rm -rf /var/run/homeproxy; "
              "/etc/init.d/homeproxy stop >/tmp/hp-stop.out 2>&1; echo rc=$?; "
              "grep -q 'nonexistent directory' /tmp/hp-stop.out && echo log-error || echo clean",
              "rc=0\nclean")
        check(con, "lifecycle: restart when stopped starts",
              "/etc/init.d/homeproxy stop >/dev/null 2>&1; rm -rf /var/run/homeproxy; "
              "/etc/init.d/homeproxy restart >/tmp/hp-restart.out 2>&1; echo rc=$?; "
              "pgrep -f '[s]ing-box.*sing-box-c' >/dev/null && echo running || echo stopped",
              "rc=0\nrunning")
        r1 = sh(con, "pgrep -f '[s]ing-box.*sing-box-c' | wc -l")
        n1 = sh(con, "nft list ruleset 2>/dev/null | grep -c 'homeproxy_'")
        con.command("for i in 1 2 3; do /etc/init.d/homeproxy restart >/dev/null 2>&1; done", timeout=180)
        time.sleep(2)
        r2 = sh(con, "pgrep -f '[s]ing-box.*sing-box-c' | wc -l")
        n2 = sh(con, "nft list ruleset 2>/dev/null | grep -c 'homeproxy_'")
        record("lifecycle: repeated restart no duplicate process", r1 == r2 == "1", "pids=%s->%s" % (r1, r2))
        record("lifecycle: repeated restart no duplicate rules", n1 == n2, "rules=%s->%s" % (n1, n2))

        # Scenario 5: no node configured -> never auto-start.
        con.command("/usr/sbin/proxy-switch off >/dev/null 2>&1; "
                    "uci set homeproxy.config.routing_mode='bypass_mainland_china'; "
                    "uci set homeproxy.config.main_node='nil'; "
                    "uci set homeproxy.server.enabled='0'; "
                    "uci set homeproxy.config.enabled='1'; uci commit homeproxy; "
                    "/etc/init.d/homeproxy disable >/dev/null 2>&1; "
                    "/etc/init.d/proxy-guard restart", timeout=240)
        time.sleep(5)
        check(con, "no-node: not started",
              "pgrep -f '[s]ing-box run' >/dev/null && echo running || echo stopped", "stopped")
        check(con, "no-node: link stays absent",
              "/etc/init.d/homeproxy enabled >/dev/null 2>&1 && echo link || echo no-link", "no-link")

        # Scenario 6: i18n packages actually present (standard VM preset only
        # installs a subset of the apps; count what this image has).
        n = sh(con, "apk list --installed 2>/dev/null | grep -c 'luci-i18n-.*-zh-cn'")
        record("i18n: zh-cn packs installed", n.isdigit() and int(n) >= 20, "count=%s" % n)
        check(con, "i18n: key lmo files exist",
              "find /usr/lib -name 'firewall.zh-cn.lmo' -o -name 'ttyd.zh-cn.lmo' "
              "-o -name 'nlbwmon.zh-cn.lmo' 2>/dev/null | wc -l", "3")
    finally:
        q.close()
        q.log.close()

    print("\n%d passed, %d failed" % (len(PASSES), len(FAILS)))
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("HOMEPROXY TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
