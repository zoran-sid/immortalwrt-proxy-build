#!/usr/bin/env python3
"""Real x86 image checks: baked generator, core boot, DDNS link recovery.

Uses a local dummy SOCKS node for schema/process checks only. It does not
claim external proxy data-plane or hardware compatibility validation.
"""
import argparse
import os
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from qemu_homeproxy import Qemu, sh  # noqa: E402


def check(console, name, command, marker):
    output = sh(console, command)
    if marker not in output:
        raise AssertionError(name + ": " + output[-1500:])
    print("PASS " + name, flush=True)


def wait_ready(console):
    # The serial login shell is available before rcS finishes first-boot UCI
    # defaults. Wait for procd's completion event before testing saved values.
    for _ in range(60):
        output = sh(console, "logread | grep -Fq -- '- init complete -' && echo BOOT_READY")
        if "BOOT_READY" in output:
            print("PASS system initialization completed", flush=True)
            return
        time.sleep(2)
    raise TimeoutError("First-boot initialization did not complete")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--log", default="qemu-firmware-boot.log")
    args = parser.parse_args()
    vm = Qemu(args.image, args.log)
    try:
        con = vm.start()
        wait_ready(con)
        check(con, "baked generator", "grep -q 'unique tags per routing node' /etc/homeproxy/scripts/generate_client.uc && echo BAKED", "BAKED")
        check(con, "maintenance startup link", "/etc/init.d/service-boot enabled && echo ENABLED", "ENABLED")
        check(con, "PassWall five-second default", "uci -q get 'passwall.@global_delay[0].start_delay'", "5")
        assignments = [
            "homeproxy.config.routing_mode=custom", "homeproxy.config.enabled=1",
            "homeproxy.routing=homeproxy", "homeproxy.routing.default_outbound=test_a",
            "homeproxy.routing.default_outbound_dns=default-dns",
            "homeproxy.dns=homeproxy", "homeproxy.dns.default_server=default-dns",
            "homeproxy.dns.default_strategy=ipv4_only",
            "homeproxy.test_node=node", "homeproxy.test_node.type=socks",
            "homeproxy.test_node.address=127.0.0.1", "homeproxy.test_node.port=1080",
            "homeproxy.test_node.socks_version=5",
        ]
        for name in ("test_a", "test_b"):
            assignments += [f"homeproxy.{name}=routing_node", f"homeproxy.{name}.enabled=1",
                            f"homeproxy.{name}.node=test_node"]
        assignments += ["homeproxy.test_u=routing_node", "homeproxy.test_u.enabled=1",
                        "homeproxy.test_u.node=urltest", "homeproxy.test_u.urltest_interval=300",
                        "homeproxy.test_u.urltest_tolerance=50", "homeproxy.test_u.urltest_idle_timeout=1800"]
        for value in assignments:
            output = sh(con, "uci set " + shlex.quote(value) + " || echo SET_FAILED")
            if "SET_FAILED" in output:
                raise AssertionError("Cannot set fixture " + value)
        check(con, "URLTest fixture uses a UCI list",
              "uci add_list homeproxy.test_u.urltest_nodes=test_node && echo LIST_OK", "LIST_OK")
        sh(con, "uci commit homeproxy")
        check(con, "real generator and sing-box schema",
              "ucode -S /etc/homeproxy/scripts/generate_client.uc && sing-box check -c /var/run/homeproxy/sing-box-c.json && echo SCHEMA_OK", "SCHEMA_OK")
        expression = ('let c=json(fs.readfile("/var/run/homeproxy/sing-box-c.json")); '
                      'let seen={}; let ok=true; for(let o in c.outbounds) { '
                      'if(seen[o.tag]) ok=false; seen[o.tag]=true; } '
                      'print(ok ? "UNIQUE_TAGS" : "DUPLICATE_TAGS");')
        check(con, "shared real node has unique outbound tags", "ucode -lfs -e " + shlex.quote(expression), "UNIQUE_TAGS")
        sh(con, "/usr/sbin/proxy-switch homeproxy")
        check(con, "HomeProxy core starts", "sleep 5; pgrep -f '[s]ing-box[[:space:]]+run[[:space:]].*sing-box-c.json' && echo CORE_RUNNING", "CORE_RUNNING")
        # Simulate a plugin UI saving enabled=1 without creating a startup link.
        sh(con, "/etc/init.d/service-boot stop")
        sh(con, "uci set ddns-go.config.enabled=1; uci commit ddns-go; /etc/init.d/ddns-go disable")
        vm.poweroff()
        con = vm.start()
        wait_ready(con)
        check(con, "HomeProxy choice persists after reboot", "uci -q get proxy-guard.main.active", "homeproxy")
        check(con, "HomeProxy core returns after reboot", "sleep 10; pgrep -f '[s]ing-box[[:space:]]+run[[:space:]].*sing-box-c.json' && echo CORE_RUNNING", "CORE_RUNNING")
        check(con, "enabled DDNS startup link recovered", "/etc/init.d/ddns-go enabled && echo DDNS_ENABLED", "DDNS_ENABLED")
        check(con, "DDNS process after reboot", "pgrep -f '[d]dns-go.*config.yaml' && echo DDNS_RUNNING", "DDNS_RUNNING")
        print("All firmware boot checks passed (no external proxy traffic asserted).", flush=True)
    finally:
        vm.close()


if __name__ == "__main__":
    main()
