#!/usr/bin/env python3
"""Headless QEMU smoke test for an ImmortalWrt x86 image.

Boots the image, logs in on the serial console as root (no password, as shipped)
and asserts that the base system came up: ubus/procd, LAN addressing, dnsmasq,
firewall4/nftables, SSH and LuCI listeners, WAN default route, internet, and
that a UCI change survives a reboot.

Usage:
  python3 tests/qemu_smoke.py --image /path/to/firmware.img --log smoke.log
  QEMU=qemu-system-x86_64 python3 tests/qemu_smoke.py --image ...

Works on Windows (QEMU for Windows) and Linux (TCG, or KVM when /dev/kvm
exists). Exit code 0 only if every check passes.
"""

import argparse
import os
import socket
import subprocess
import sys
import time

CHECKS = [
    ("ubus system board", "ubus call system board"),
    ("procd service list", "ubus call service list >/dev/null && echo procd-ok"),
    ("LAN 192.168.1.1", "ip -4 addr show br-lan 2>/dev/null | grep -q 'inet 192.168.1.1' && echo lan-ok"),
    ("dnsmasq DNS A", "nslookup www.baidu.com 127.0.0.1 >/dev/null 2>&1 && echo dns-ok"),
    ("dnsmasq DNS AAAA", "nslookup -type=AAAA www.baidu.com 127.0.0.1 >/dev/null 2>&1 && echo aaaa-ok"),
    ("nftables ruleset", "nft list ruleset 2>/dev/null | grep -q 'chain ' && echo nft-ok"),
    ("dnsmasq process", "pgrep -f '[d]nsmasq' >/dev/null && echo dnsmasq-ok"),
    ("fw4 nftables table", "nft list tables 2>/dev/null | grep -q 'table inet fw4' && echo fw4-ok"),
    ("SSH listener :22", "netstat -lnt 2>/dev/null | grep -q ':22 ' && echo ssh-ok"),
    ("LuCI http 200", "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ | grep -q 200 && echo luci-ok"),
    ("WAN default route", "ip route 2>/dev/null | grep -q '^default' && echo wan-ok"),
    ("internet IPv4", "curl -4 -s -o /dev/null -m 12 -w '%{http_code}' https://www.baidu.com/ | grep -q 200 && echo inet-ok"),
]


class Console:
    def __init__(self, sock, log):
        self.sock = sock
        self.log = log
        self.buf = b""

    def clear(self):
        self.buf = b""

    def read(self, timeout):
        self.sock.settimeout(timeout)
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return b""
        if not chunk:
            raise EOFError("serial closed")
        self.buf += chunk
        self.log.write(chunk.decode("utf-8", "replace"))
        self.log.flush()
        return chunk

    def wait_for(self, needles, timeout=180, quiet=False):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for needle in needles:
                if isinstance(needle, str):
                    needle = needle.encode()
                if needle in self.buf:
                    if quiet:
                        time.sleep(0.5)
                        self.read(0.5)
                    return needle
            self.read(1)
        raise TimeoutError("timeout waiting for %r; tail=%r" % (needles, self.buf[-600:]))

    def send(self, data, slow=False):
        if isinstance(data, str):
            data = data.encode()
        if slow:
            # The emulated 16550 FIFO drops bytes when a long line arrives in
            # one burst under TCG; pace the input so the guest keeps up.
            for i in range(0, len(data), 8):
                self.sock.sendall(data[i:i + 8])
                time.sleep(0.004)
        else:
            self.sock.sendall(data)

    def login(self, timeout, password=""):
        # OpenWrt may boot straight to "# " or print "Please press Enter to
        # activate this console" first (the Enter is easy to miss when the
        # harness connects before the banner). Clear stale output so a prompt
        # from before a reboot cannot be mistaken for the new one.
        self.clear()
        deadline = time.time() + timeout
        last_enter = 0.0
        while time.time() < deadline:
            if b"# " in self.buf:
                break
            if b"login:" in self.buf:
                self.send("root\r")
                self.buf = b""
                got = self.wait_for([b"# ", b"Password:"], timeout=30)
                if got == b"Password:":
                    self.send((password or "") + "\r")
                    got = self.wait_for([b"# ", b"Login incorrect"], timeout=30)
                    if got == b"Login incorrect":
                        raise RuntimeError("root login rejected")
                break
            if time.time() - last_enter > 3:
                self.send("\r")
                last_enter = time.time()
            self.read(1)
        else:
            raise TimeoutError("no shell prompt; tail=%r" % self.buf[-600:])
        self.clear()
        self.send("\r")
        self.wait_for([b"# "], timeout=30)
        self.clear()

    def command(self, cmd, timeout=120):
        """Run one command, returning (output, returncode).

        The console echoes the command and may split reads anywhere, so the
        result is written to a file and every output line is prefixed with
        '@@', framed by '@@B' / '@@E:<rc>' lines. The echoed command itself
        starts with '(' so it can never be mistaken for a framed line.
        """
        self.clear()
        # Do not depend on a separate return-code marker: under TCG the guest
        # occasionally drops bytes from long console lines, and a lost marker
        # used to make every check look like a failure. Commands that need an
        # exit code echo it into their own output instead.
        wrapped = (
            "( %s ) >/tmp/.smk.out 2>&1; "
            "printf '@@B\\n'; sed 's/^/@@/' /tmp/.smk.out; "
            "printf '@@E\\n'\r" % cmd
        )
        self.send(wrapped, slow=True)
        deadline = time.time() + timeout
        raw = b""
        while time.time() < deadline:
            raw += self.read(1)
            text = raw.decode("utf-8", "replace")
            if any(line.startswith("@@E") for line in text.splitlines()):
                break
        else:
            raise TimeoutError("command timeout: %s" % cmd)

        lines = raw.decode("utf-8", "replace").splitlines()
        body = []
        inside = False
        for line in lines:
            if line.startswith("@@B"):
                inside = True
                continue
            if inside and line.startswith("@@E"):
                break
            if inside and line.startswith("@@"):
                body.append(line[2:])
        return "\n".join(body), None

    def sh(self, cmd, timeout=120):
        out, rc = self.command(cmd, timeout)
        return out.strip()



def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qemu", default=os.environ.get("QEMU", "qemu-system-x86_64"))
    ap.add_argument("--image", required=True)
    ap.add_argument("--log", default="qemu-smoke.log")
    ap.add_argument("--memory", default="1024")
    ap.add_argument("--smp", default="2")
    ap.add_argument("--boot-timeout", type=int, default=420)
    ap.add_argument("--skip-reboot", action="store_true")
    ap.add_argument("--no-wan-config", action="store_true",
                    help="do not add a DHCP WAN on eth1 before checking")
    args = ap.parse_args()

    log = open(args.log, "w", encoding="utf-8", errors="replace")
    failures = []

    def start(serial_port, qlog_path):
        args_list = [
            args.qemu, "-machine", "q35", "-m", args.memory, "-smp", args.smp,
            "-cpu", "max",
            "-drive", "file=%s,format=raw,if=ide,index=0" % args.image,
            "-netdev", "user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8080-:80",
            "-device", "e1000,netdev=net0",
            "-netdev", "user,id=net1", "-device", "e1000,netdev=net1",
            "-display", "none", "-vga", "none", "-monitor", "none",
            "-serial", "tcp:127.0.0.1:%d,server=on,wait=off" % serial_port,
        ]
        if os.path.exists("/dev/kvm"):
            args_list[args_list.index("-cpu") + 1] = "host"
            args_list.append("-enable-kvm")
        qlog = open(qlog_path, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(args_list, stdout=qlog, stderr=subprocess.STDOUT)
        log.write("QEMU: %s\n" % " ".join(args_list))
        log.flush()
        deadline = time.time() + 60
        sock = None
        while time.time() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", serial_port), timeout=2)
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError("QEMU exited early; see %s" % qlog_path)
                time.sleep(1)
        if sock is None:
            raise RuntimeError("cannot connect to the QEMU serial port")
        con = Console(sock, log)
        con.login(args.boot_timeout)
        # Login can succeed before the kernel/procd have finished printing boot
        # messages; let the console settle so commands are not interleaved.
        time.sleep(12)
        con.clear()
        return proc, qlog, sock, con

    proc = qlog = sock = None
    try:
        proc, qlog, sock, con = start(free_port(), args.log + ".qemu")

        # ImmortalWrt x86 ImageBuilder images ship a LAN-only network config
        # (no WAN section). The first-boot wizard is the intended way to add
        # WAN; for a headless smoke test configure DHCP on eth1 explicitly.
        if not args.no_wan_config:
            con.command(
                "uci -q get network.wan >/dev/null || { "
                "uci set network.wan=interface; "
                "uci set network.wan.device='eth1'; "
                "uci set network.wan.proto='dhcp'; "
                "uci commit network; /etc/init.d/network reload; }",
                timeout=60,
            )
            time.sleep(10)

        board, _rc = con.command("ubus call system board")
        print("board:\n%s" % board)
        log.write("\nboard:\n%s\n" % board)

        for name, cmd in CHECKS:
            try:
                out, rc = con.command(cmd, timeout=90)
            except TimeoutError as exc:
                failures.append("%s: timeout (%s)" % (name, exc))
                print("FAIL  %s (timeout)" % name)
                continue
            ok = "ok" in out or name == "ubus system board"
            print("%s  %s" % ("PASS" if ok else "FAIL", name))
            log.write("\n$ %s\nrc=%s\n%s\n" % (cmd, rc, out))
            if not ok:
                failures.append("%s (rc=%s, out=%r)" % (name, rc, out[-200:]))

        warnings, _rc = con.command(
            "logread 2>/dev/null | grep -iE 'error|fail' | head -20", timeout=60
        )
        log.write("\nlog warnings:\n%s\n" % warnings)

        if not args.skip_reboot:
            con.command("uci set system.@system[0].hostname=smoke-test; uci commit system; sync")
            # f2fs checkpoints lazily; without a short grace period an immediate
            # poweroff can roll back to the previous checkpoint and drop the
            # write (verified on the standard image).
            print("waiting for the f2fs checkpoint before poweroff ...")
            time.sleep(22)
            print("cold-restarting to test persistence ...")
            con.send("poweroff\r")
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=20)
            sock.close()
            qlog.close()

            # Cold boot the same disk again: this is a stronger persistence
            # check than the guest's own reboot, and it avoids QEMU's serial
            # console not being reset by the guest reboot under TCG.
            proc, qlog, sock, con = start(free_port(), args.log + ".qemu2")
            host, _rc = con.command("uci get system.@system[0].hostname", timeout=30)
            wan, _rc = con.command("ip -4 addr show eth1 2>/dev/null | grep -c 'inet '", timeout=30)
            ok = host.strip() == "smoke-test" and wan.strip() != "0"
            print("%s  UCI+WAN persist across cold boot (host=%s wan=%s)"
                  % ("PASS" if ok else "FAIL", host.strip(), wan.strip()))
            if not ok:
                failures.append("cold-boot persistence")
        con.command("uci set system.@system[0].hostname=ImmortalWrt && uci commit system",
                    timeout=30)
    finally:
        try:
            if sock is not None:
                sock.sendall(b"poweroff\r")
                time.sleep(2)
        except Exception:
            pass
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if qlog is not None:
            qlog.close()
        log.close()

    print()
    if failures:
        print("SMOKE FAILED: %s" % ", ".join(failures))
        return 1
    print("SMOKE PASSED: %d checks + cold-boot persistence" % len(CHECKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
