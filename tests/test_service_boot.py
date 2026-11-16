"""Enabled plugin settings restore missing startup links without restarts."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SH = os.environ.get("TEST_SH") or shutil.which("sh")


def posix(path):
    text = Path(path).as_posix()
    return "/" + text[0].lower() + text[2:] if os.name == "nt" else text


@unittest.skipUnless(SH, "POSIX shell required")
class ServiceBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("bin", "etc/init.d", "etc/config", "lib", "state", "usr/share/service-boot"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, TEST_ROOT=posix(self.root))
        source = (ROOT / "modules/base/files/usr/sbin/service-boot-reconcile").read_text(encoding="utf-8")
        for path in ("/etc/init.d", "/etc/config", "/lib/functions.sh", "/usr/share/service-boot"):
            source = source.replace(path, posix(self.root) + path)
        self.write("reconcile", source)
        self.write("lib/functions.sh", '''config_load() { CURRENT_PACKAGE="$1"; }
config_get_bool() {
 value=$(cat "$TEST_ROOT/state/$CURRENT_PACKAGE.$2.$3" 2>/dev/null)
 case "$value" in 1|on|true|yes) value=1 ;; *) value=0 ;; esac
 eval "$1=\\$value"
}
config_foreach() {
 while read -r kind section; do
  if [ "$kind" = "$2" ]; then "$1" "$section"; fi
 done < "$TEST_ROOT/state/$CURRENT_PACKAGE.sections"
}
''')
        self.write("bin/logger", "#!/bin/sh\nexit 0\n")
        shutil.copyfile(ROOT / "modules/base/files/usr/share/service-boot/services.conf",
                        self.root / "usr/share/service-boot/services.conf")

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def service(self, name, package, section, enabled, option="enabled", kind=None):
        self.write("etc/config/" + package, "fixture\n")
        self.write("state/" + package + "." + section + "." + option, enabled)
        if kind:
            self.write("state/" + package + ".sections", kind + " " + section + "\n")
        self.write("etc/init.d/" + name, '''#!/bin/sh
svc=${0##*/}
case "$1" in
 enabled) test -f "$TEST_ROOT/state/$svc.link" ;;
 enable) echo "$svc enable" >> "$TEST_ROOT/actions"; touch "$TEST_ROOT/state/$svc.link" ;;
 start) echo "$svc start" >> "$TEST_ROOT/actions" ;;
esac
''')

    def reconcile(self):
        result = subprocess.run([SH, "-c", 'export PATH="$TEST_ROOT/bin:$PATH"; exec "$@"',
                                 "test", posix(self.root / "reconcile")],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enabled_ddns_recovers_missing_link_once(self):
        self.service("ddns-go", "ddns-go", "config", "1")
        self.reconcile()
        self.reconcile()
        self.assertEqual((self.root / "actions").read_text().splitlines(),
                         ["ddns-go enable", "ddns-go start"])

    def test_disabled_service_is_not_started(self):
        self.service("adguardhome", "adguardhome", "config", "0")
        self.reconcile()
        self.assertFalse((self.root / "actions").exists())
        self.assertFalse((self.root / "state/adguardhome.link").exists())

    def test_existing_link_is_left_to_native_boot(self):
        self.service("ddns-go", "ddns-go", "config", "1")
        self.write("state/ddns-go.link", "")
        self.reconcile()
        self.assertFalse((self.root / "actions").exists())

    def test_multiple_instance_enable_option_is_supported(self):
        self.service("natmap", "natmap", "instance", "1", option="enable", kind="natmap")
        self.reconcile()
        self.assertEqual((self.root / "actions").read_text().splitlines(),
                         ["natmap enable", "natmap start"])
