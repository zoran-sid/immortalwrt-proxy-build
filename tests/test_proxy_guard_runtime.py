"""Execute the shipped shell scripts against isolated fake router commands."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SH = os.environ.get("TEST_SH") or shutil.which("sh")


def shellpath(path):
    text = Path(path).as_posix()
    if len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


@unittest.skipUnless(SH, "POSIX shell required (set TEST_SH on Windows)")
class GuardRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("bin", "sbin", "init", "lock", "run", "state"):
            (self.root / name).mkdir()
        self.env = dict(os.environ, TEST_ROOT=shellpath(self.root),
                        PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"])
        for name in ("proxy-switch", "proxy-guard-reconcile", "homeproxy-boot-retry"):
            content = (ROOT / "modules/proxy-guard/files/usr/sbin" / name).read_text(encoding="utf-8")
            for source, dest in (("/etc/init.d", "init"), ("/usr/sbin", "sbin"),
                                 ("/var/run", "run"), ("/var/lock", "lock")):
                content = content.replace(source, shellpath(self.root / dest))
            self.write("sbin/" + name, content)
        self.write("bin/uci", '''#!/bin/sh
[ "$1" = -q ] && shift
case "$1" in
get) cat "$TEST_ROOT/state/$2" 2>/dev/null ;;
set) key=${2%%=*}; test ! -f "$TEST_ROOT/state/$key.write-fails" || exit 1
 printf '%s' "${2#*=}" > "$TEST_ROOT/state/$key" ;;
commit) echo commit >> "$TEST_ROOT/commits" ;;
esac
''')
        self.write("bin/pgrep", '''#!/bin/sh
if [ -f "$TEST_ROOT/cmdlines" ]; then
 {
  cat "$TEST_ROOT/cmdlines"
  if [ -f "$TEST_ROOT/state/homeproxy.running" ]; then
   echo '/usr/bin/sing-box run --config /var/run/homeproxy/sing-box-c.json'
  fi
 } | grep -E -- "$2" >/dev/null
 exit $?
fi
case "$*" in
*sing-box-*) svc=homeproxy ;;
*passwall*) svc=passwall ;;
*) exit 1 ;;
esac
[ -f "$TEST_ROOT/state/$svc.running" ]
''')
        self.write("bin/nft", '''#!/bin/sh
case "$1" in
list) test -f "$TEST_ROOT/state/$4.table" ;;
delete) echo "$4" >> "$TEST_ROOT/deleted"; rm -f "$TEST_ROOT/state/$4.table" ;;
esac
''')
        for name in ("logger", "chown"):
            self.write("bin/" + name, "#!/bin/sh\nexit 0\n")
        for svc in ("homeproxy", "passwall"):
            self.write("init/" + svc, '''#!/bin/sh
svc=${0##*/}
case "$1" in
enabled) test -f "$TEST_ROOT/state/$svc.link" ;;
enable) touch "$TEST_ROOT/state/$svc.link" ;;
disable) rm -f "$TEST_ROOT/state/$svc.link" ;;
stop) rm -f "$TEST_ROOT/state/$svc.running" "$TEST_ROOT/state/$svc.table" ;;
start|restart)
 echo "$svc $1" >> "$TEST_ROOT/starts"
 test ! -f "$TEST_ROOT/state/$svc.fail" || exit 1
 touch "$TEST_ROOT/state/$svc.running" "$TEST_ROOT/state/$svc.table"
 ;;
esac
''')
        self.put("proxy-guard.main.state", "normal")
        self.put("proxy-guard.main.active", "homeproxy")
        self.put("homeproxy.config.enabled", "1")
        self.put("homeproxy.config.main_node", "test-node")
        self.put("passwall.@global[0].enabled", "0")

    def write(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def put(self, key, value=""):
        (self.root / "state" / key).write_text(value)

    def exists(self, key):
        return (self.root / "state" / key).exists()

    def run_script(self, name, *args):
        # Git Bash prepends its system commands to inherited Windows PATH.
        # Prepend stubs inside the shell so sleep/pgrep really are isolated.
        return subprocess.run([SH, "-c", 'export PATH="$TEST_ROOT/bin:$PATH"; exec "$@"',
                               "router-test", shellpath(self.root / "sbin" / name), *args],
                              env=self.env, capture_output=True, text=True, timeout=20)

    def test_repeated_events_preserve_live_firewall_and_core(self):
        self.put("homeproxy.running")
        self.put("homeproxy.table")
        self.put("homeproxy.link")
        for _ in range(2):
            self.assertEqual(self.run_script("proxy-guard-reconcile", "--event").returncode, 0)
        self.assertTrue(self.exists("homeproxy.table"))
        self.assertTrue(self.exists("homeproxy.running"))
        self.assertFalse((self.root / "starts").exists())
        self.assertFalse((self.root / "commits").exists())

    def test_failed_enable_write_does_not_stop_working_frontend_or_record_success(self):
        self.put("homeproxy.config.enabled", "0")
        self.put("homeproxy.config.enabled.write-fails")
        self.put("passwall.running")
        self.put("proxy-guard.main.active", "passwall")
        result = self.run_script("proxy-switch", "homeproxy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot save homeproxy enabled state", result.stderr)
        self.assertTrue(self.exists("passwall.running"))
        self.assertFalse(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "state/proxy-guard.main.active").read_text(), "passwall")

    def test_failed_start_can_retry_after_network_recovers(self):
        self.put("homeproxy.fail")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertFalse(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "state/proxy-guard.main.active").read_text(), "homeproxy")
        (self.root / "state/homeproxy.fail").unlink()
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertTrue(self.exists("homeproxy.running"))
        self.assertTrue(self.exists("homeproxy.table"))
        self.assertTrue(self.exists("homeproxy.link"))

    def test_event_during_failopen_respects_backoff(self):
        self.put("proxy-guard.main.state", "failopen")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertFalse(self.exists("homeproxy.running"))
        self.assertFalse((self.root / "starts").exists())

    def test_reboot_retries_persisted_failopen(self):
        self.put("proxy-guard.main.state", "failopen")
        self.put("homeproxy.link")
        self.run_script("proxy-guard-reconcile", "--boot")
        self.assertEqual((self.root / "state/proxy-guard.main.state").read_text(), "normal")
        self.assertFalse((self.root / "starts").exists())
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertTrue(self.exists("homeproxy.running"))

    def test_custom_without_outbound_does_not_start(self):
        self.put("homeproxy.config.main_node", "nil")
        self.put("homeproxy.config.routing_mode", "custom")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertFalse((self.root / "starts").exists())

    def test_custom_disabled_outbound_ignores_stale_main_node(self):
        self.put("homeproxy.config.routing_mode", "custom")
        self.put("homeproxy.routing.default_outbound", "nil")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertFalse((self.root / "starts").exists())

    def test_ui_enabling_modern_passwall_replaces_old_homeproxy_choice(self):
        self.put("homeproxy.running")
        self.put("passwall.@global[0].enabled", "1")
        self.put("passwall.@global[0].node", "modern-node")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertTrue(self.exists("passwall.running"))
        self.assertFalse(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "state/proxy-guard.main.active").read_text(), "passwall")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertEqual((self.root / "starts").read_text().splitlines(), ["passwall start"])

    def test_modern_disabled_passwall_does_not_use_stale_tcp_node(self):
        self.put("passwall.@global[0].enabled", "1")
        self.put("passwall.@global[0].node", "nil")
        self.put("passwall.@global[0].tcp_node", "stale-node")
        self.put("homeproxy.running")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertFalse(self.exists("passwall.running"))
        self.assertTrue(self.exists("homeproxy.running"))

    def test_ui_enabling_homeproxy_replaces_old_passwall_choice(self):
        self.put("proxy-guard.main.active", "passwall")
        self.put("passwall.@global[0].enabled", "1")
        self.put("passwall.@global[0].node", "modern-node")
        self.put("passwall.running")
        self.run_script("proxy-guard-reconcile", "--event")
        self.assertTrue(self.exists("homeproxy.running"))
        self.assertFalse(self.exists("passwall.running"))

    def test_ui_disabling_last_frontend_stops_it_and_records_off(self):
        for active in ("homeproxy", "passwall"):
            self.put("proxy-guard.main.active", active)
            self.put("homeproxy.config.enabled", "0")
            self.put("passwall.@global[0].enabled", "0")
            self.put(active + ".running")
            self.put(active + ".link")
            self.run_script("proxy-guard-reconcile", "--event")
            self.assertFalse(self.exists(active + ".running"))
            self.assertFalse(self.exists(active + ".link"))
            self.assertEqual((self.root / "state/proxy-guard.main.active").read_text(), "off")

    def test_native_homeproxy_apply_enables_without_private_flag(self):
        self.put("homeproxy.config.enabled", "0")
        self.put("proxy-guard.main.active", "off")
        self.run_script("proxy-guard-reconcile", "--event", "homeproxy")
        self.assertTrue(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "state/homeproxy.config.enabled").read_text(), "1")

    def test_native_homeproxy_disable_stops_and_clears_private_flag(self):
        self.put("homeproxy.config.main_node", "nil")
        self.put("homeproxy.running")
        self.run_script("proxy-guard-reconcile", "--event", "homeproxy")
        self.assertFalse(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "state/homeproxy.config.enabled").read_text(), "0")

    def test_switch_away_preserves_native_exit_for_switch_back(self):
        self.run_script("proxy-switch", "passwall")
        self.assertEqual((self.root / "state/homeproxy.config.main_node").read_text(), "nil")
        self.run_script("proxy-switch", "homeproxy")
        self.assertEqual((self.root / "state/homeproxy.config.main_node").read_text(), "test-node")
        self.assertTrue(self.exists("homeproxy.running"))

    def test_ensure_does_not_restore_native_settings_edited_while_enabled(self):
        self.put("homeproxy.server.enabled", "0")
        self.put("proxy-guard.main.hp_saved_enabled", "1")
        self.put("homeproxy.running")
        self.run_script("proxy-switch", "--ensure", "homeproxy")
        self.assertEqual((self.root / "state/homeproxy.server.enabled").read_text(), "0")
        self.assertFalse((self.root / "commits").exists())

    def test_homeproxy_core_does_not_count_as_running_passwall(self):
        self.put("homeproxy.running")
        self.put("homeproxy.link")
        self.put("passwall.@global[0].tcp_node", "test-node")
        result = self.run_script("proxy-switch", "--ensure", "passwall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.exists("passwall.running"))
        self.assertFalse(self.exists("homeproxy.running"))
        self.assertFalse(self.exists("homeproxy.link"))
        self.assertTrue(self.exists("passwall.link"))
        self.assertEqual((self.root / "state/homeproxy.config.enabled").read_text(), "0")
        self.assertEqual((self.root / "state/passwall.@global[0].enabled").read_text(), "1")
        self.assertEqual((self.root / "state/proxy-guard.main.active").read_text(), "passwall")
        self.assertEqual(self.run_script("proxy-guard-reconcile", "--event").returncode, 0)
        self.assertTrue(self.exists("passwall.running"))
        self.assertTrue(self.exists("passwall.table"))
        self.assertFalse(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "starts").read_text().splitlines(), ["passwall start"])

    def test_network_events_support_custom_and_uppercase_interface_names(self):
        hook = (ROOT / "modules/proxy-guard/files/etc/hotplug.d/iface/99-proxy-guard").read_text(encoding="utf-8")
        hook = hook.replace("/usr/sbin", shellpath(self.root / "sbin"))
        self.write("sbin/test-hotplug", hook)
        self.write("sbin/proxy-guard-reconcile", '#!/bin/sh\necho "$INTERFACE" >> "$TEST_ROOT/events"\n')
        for interface in ("wan", "WAN", "uplink"):
            self.env.update(ACTION="ifup", INTERFACE=interface)
            self.assertEqual(self.run_script("test-hotplug").returncode, 0)
        self.env.update(ACTION="ifdown", INTERFACE="wan")
        self.assertEqual(self.run_script("test-hotplug").returncode, 0)
        self.assertEqual((self.root / "events").read_text().splitlines(), ["wan", "WAN", "uplink"])

    def fake_sleep(self, extra=""):
        self.write("bin/sleep", '#!/bin/sh\necho "$1" >> "$TEST_ROOT/sleeps"\n' + extra)

    def test_homeproxy_config_check_does_not_count_as_running_core(self):
        (self.root / "cmdlines").write_text(
            "/usr/bin/sing-box check -c /var/run/homeproxy/sing-box-c.json\n")
        result = self.run_script("proxy-switch", "--ensure", "homeproxy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "starts").read_text().splitlines(), ["homeproxy start"])

    def test_passwall_dns_helper_does_not_count_as_running_core(self):
        (self.root / "cmdlines").write_text(
            "/tmp/etc/passwall/bin/chinadns-ng -C /tmp/etc/passwall/dns.conf\n")
        result = self.run_script("proxy-switch", "--ensure", "passwall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "starts").read_text().splitlines(), ["passwall start"])

    def test_homeproxy_run_process_is_preserved(self):
        (self.root / "cmdlines").write_text(
            "/usr/bin/sing-box run --config /var/run/homeproxy/sing-box-c.json\n")
        self.put("homeproxy.running")
        self.put("homeproxy.table")
        self.assertEqual(self.run_script("proxy-switch", "--ensure", "homeproxy").returncode, 0)
        self.assertFalse((self.root / "starts").exists())
        self.assertTrue(self.exists("homeproxy.running"))
        self.assertTrue(self.exists("homeproxy.table"))

    def test_homeproxy_boot_worker_starts_at_five_second_retry(self):
        self.fake_sleep()
        result = self.run_script("homeproxy-boot-retry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "starts").read_text().splitlines(), ["homeproxy start"])
        self.assertEqual((self.root / "sleeps").read_text().splitlines(), ["5", "5"])

    def test_homeproxy_boot_worker_preserves_passwall_choice(self):
        self.put("homeproxy.config.enabled", "0")
        self.put("proxy-guard.main.active", "passwall")
        self.put("passwall.running")
        self.fake_sleep()
        self.assertEqual(self.run_script("homeproxy-boot-retry").returncode, 0)
        self.assertFalse((self.root / "starts").exists())
        self.assertTrue(self.exists("passwall.running"))

    def test_homeproxy_boot_worker_respects_failopen(self):
        self.put("proxy-guard.main.state", "failopen")
        self.fake_sleep()
        self.assertEqual(self.run_script("homeproxy-boot-retry").returncode, 0)
        self.assertFalse((self.root / "starts").exists())

    def test_homeproxy_boot_worker_does_not_interrupt_native_start(self):
        (self.root / "cmdlines").write_text(
            "/bin/sh /etc/rc.common /etc/rc.d/S99homeproxy boot\n")
        self.fake_sleep('''count=$(wc -l < "$TEST_ROOT/sleeps")
if [ "$count" -eq 2 ]; then : > "$TEST_ROOT/cmdlines"; fi
''')
        result = self.run_script("homeproxy-boot-retry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "starts").read_text().splitlines(), ["homeproxy start"])
        self.assertEqual((self.root / "sleeps").read_text().splitlines(), ["5", "5", "5"])

    def test_homeproxy_boot_worker_retries_failed_start(self):
        self.put("homeproxy.fail")
        self.fake_sleep('''count=$(wc -l < "$TEST_ROOT/sleeps")
if [ "$count" -eq 2 ]; then rm -f "$TEST_ROOT/state/homeproxy.fail"; fi
''')
        result = self.run_script("homeproxy-boot-retry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.exists("homeproxy.running"))
        self.assertEqual((self.root / "starts").read_text().splitlines(),
                         ["homeproxy start", "homeproxy start"])
        self.assertEqual((self.root / "sleeps").read_text().splitlines(), ["5", "5", "5"])


if __name__ == "__main__":
    unittest.main()
