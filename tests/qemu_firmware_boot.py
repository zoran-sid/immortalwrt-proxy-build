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


def wait_check(console, name, command, marker):
    output = ""
    for _ in range(40):
        output = sh(console, command)
        if marker in output:
            print("PASS " + name, flush=True)
            return
        time.sleep(2)
    raise AssertionError(name + ": " + output[-1500:])


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
        check(con, "fresh default frontend has its application enabled",
              "uci -q get 'passwall.@global[0].enabled'", "1")
        delay_error = None
        try:
            check(con, "PassWall five-second default", "uci -q get 'passwall.@global_delay[0].start_delay'", "5")
        except AssertionError as error:
            delay_error = error
            print("FAIL " + str(error), flush=True)
            print(sh(con, "ls /etc/uci-defaults; uci -q get wizard.main.done; find /rom/etc/uci-defaults -maxdepth 1 -iname '*passwall*' -print -exec cat '{}' ';'"), flush=True)
        # CLI UCI staging is globally visible. Pause coordination while building
        # a multi-command fixture, then test real apply events with it running.
        sh(con, "mv /etc/hotplug.d/iface/99-proxy-guard /tmp/99-proxy-guard.fixture; /etc/init.d/proxy-guard stop")
        wait_check(con, "fixture coordination idle", "test ! -d /var/lock/proxy-switch.lock && echo IDLE", "IDLE")
        check(con, "fixture starts with no selected runtime", "/usr/sbin/proxy-switch off && echo OFF_READY", "OFF_READY")
        assignments = [
            "homeproxy.config.routing_mode=custom", "homeproxy.config.enabled=0",
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
        check(con, "initial HomeProxy activation", "/usr/sbin/proxy-switch homeproxy && echo SWITCH_OK", "SWITCH_OK")
        wait_check(con, "HomeProxy core starts", "pgrep -f '[s]ing-box[[:space:]]+run[[:space:]].*sing-box-c.json' && echo CORE_RUNNING", "CORE_RUNNING")
        sh(con, "mv /tmp/99-proxy-guard.fixture /etc/hotplug.d/iface/99-proxy-guard; /etc/init.d/proxy-guard start")
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
        sh(con, "uci set homeproxy.routing.default_outbound=nil; uci commit homeproxy; ubus send config.change '{\"package\":\"homeproxy\"}'; sleep 15")
        wait_check(con, "HomeProxy UI master off records off", "uci -q get proxy-guard.main.active", "off")
        wait_check(con, "HomeProxy UI master off stops core", "pgrep -f '[s]ing-box[[:space:]]+run[[:space:]].*sing-box-c.json' >/dev/null || echo STOPPED", "STOPPED")
        sh(con, "uci set homeproxy.routing.default_outbound=test_a; uci commit homeproxy; ubus send config.change '{\"package\":\"homeproxy\"}'; sleep 15")
        # Exercise the current PassWall schema and both UI directions, then
        # reboot with PassWall selected. No external proxy traffic is asserted.
        pw = ["passwall.boot_fixture=nodes", "passwall.boot_fixture.type=Xray",
              "passwall.boot_fixture.protocol=vmess", "passwall.boot_fixture.address=127.0.0.1",
              "passwall.boot_fixture.port=1080", "passwall.boot_fixture.transport=tcp",
              "passwall.boot_fixture.uuid=11111111-1111-4111-8111-111111111111",
              "passwall.boot_fixture.security=auto", "passwall.boot_fixture.alter_id=0",
              "passwall.@global[0].node=boot_fixture", "passwall.@global[0].enabled=1"]
        for value in pw:
            sh(con, "uci set " + shlex.quote(value))
        sh(con, "uci commit passwall; ubus send config.change '{\"package\":\"passwall\"}'; sleep 5")
        wait_check(con, "UI switch to modern PassWall", "uci -q get proxy-guard.main.active", "passwall")
        wait_check(con, "PassWall core starts", "pgrep -f '[/]tmp/etc/passwall/bin/xray[[:space:]]' && echo XRAY_RUNNING", "XRAY_RUNNING")
        sh(con, "uci set homeproxy.routing.default_outbound=test_a; uci commit homeproxy; ubus send config.change '{\"package\":\"homeproxy\"}'; sleep 15")
        wait_check(con, "UI switch back to HomeProxy", "uci -q get proxy-guard.main.active", "homeproxy")
        wait_check(con, "HomeProxy core after reverse switch", "pgrep -f '[s]ing-box[[:space:]]+run[[:space:]].*sing-box-c.json' && echo CORE_RUNNING", "CORE_RUNNING")
        sh(con, "uci set 'passwall.@global[0].enabled=1'; uci commit passwall; ubus send config.change '{\"package\":\"passwall\"}'; sleep 5")
        wait_check(con, "PassWall choice settled before shutdown", "uci -q get proxy-guard.main.active", "passwall")
        wait_check(con, "PassWall core settled before shutdown", "pgrep -f '[/]tmp/etc/passwall/bin/xray[[:space:]]' && echo XRAY_RUNNING", "XRAY_RUNNING")
        vm.poweroff()
        con = vm.start()
        wait_ready(con)
        check(con, "PassWall selection after reboot", "uci -q get proxy-guard.main.active", "passwall")
        wait_check(con, "PassWall core returns after reboot", "pgrep -f '[/]tmp/etc/passwall/bin/xray[[:space:]]' && echo XRAY_RUNNING", "XRAY_RUNNING")
        sh(con, "uci set 'passwall.@global[0].enabled=0'; uci commit passwall; ubus send config.change '{\"package\":\"passwall\"}'; sleep 15")
        wait_check(con, "PassWall UI master off records off", "uci -q get proxy-guard.main.active", "off")
        wait_check(con, "PassWall UI master off stops core", "pgrep -f '[/]tmp/etc/passwall/bin/xray[[:space:]]' >/dev/null || echo STOPPED", "STOPPED")
        if delay_error:
            raise delay_error
        print("All firmware boot checks passed (no external proxy traffic asserted).", flush=True)
    except Exception:
        for command in ("uci -q get proxy-guard.main.active",
                        "pgrep -af 'xray|sing-box|proxy-guard'",
                        "tail -n 35 /var/run/homeproxy/homeproxy.log",
                        "tail -n 35 /tmp/log/passwall.log",
                        "logread -e proxy-guard | tail -n 35"):
            try:
                print("DIAGNOSTIC " + command + "\n" + sh(con, command, timeout=15), flush=True)
            except Exception as error:
                print("DIAGNOSTIC unavailable: " + str(error), flush=True)
        raise
    finally:
        vm.close()


if __name__ == "__main__":
    main()
