"""Run importer transaction and rollback paths against fake router commands."""
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
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("bin", "etc/config", "etc/init.d", "etc/homeproxy/scripts", "usr/sbin", "var/lock", "tmp", "root"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, TEST_ROOT=posix(self.root))
        source = (ROOT / "scripts/routemint/install.sh").read_text(encoding="utf-8")
        for path in ("/etc/config", "/etc/init.d", "/etc/homeproxy", "/usr/sbin", "/var/lock", "/tmp/routemint-", "/root/"):
            source = source.replace(path, posix(self.root) + path)
        self.write("install.sh", source)
        self.write("configure.uc", "// mock configuration program\n")
        self.write("cn-mainland.json", "{}\n")
        for name in ("homeproxy", "passwall", "proxy-guard"):
            self.write("etc/config/" + name, "original " + name + "\n")
        self.write("etc/homeproxy/scripts/generate_client.uc", "// proxy-guard: migrate the 25.12.2 generator output\n")
        self.write("bin/uci", '''#!/bin/sh
[ "$1" != -q ] || shift
case "$1 $2" in
 'get proxy-guard.main.active') echo passwall ;;
 'get homeproxy.infra.mixed_port') echo 5330 ;;
esac
''')
        self.write("bin/ucode", '''#!/bin/sh
case "$1" in
 -L) [ "$2" = "$TEST_ROOT/etc/homeproxy/scripts" ] || exit 1
     shift 2
     if [ "$1" = -S ]; then echo '{}' > "$RM_STAGE/preview.json"; fi ;;
 -c) exit 0 ;;
 -S) echo "Cannot resolve the sibling homeproxy module without its search path" >&2; exit 1 ;;
 *) echo 'new mainland profile' > "$RM_STAGE/config/homeproxy"
    echo 'ai-openai' > "$RM_STAGE/categories"
    echo '// preview' > "$RM_STAGE/generate-preview.uc" ;;
esac
''')
        self.write("bin/curl", '''#!/bin/sh
while [ "$#" -gt 0 ]; do
 if [ "$1" = -o ]; then shift; echo 'fixture rule' > "$1"; fi
 shift
done
''')
        self.write("bin/sing-box", '''#!/bin/sh
if [ "$1" = check ] && [ "${BAD_SCHEMA:-0}" = 1 ]; then exit 1; fi
exit 0
''')
        self.write("bin/pgrep", '''#!/bin/sh
case "$*" in
 *watchdog*) exit 0 ;;
 *sing-box-c*) test -f "$TEST_ROOT/core.running" ;;
 *) exit 1 ;;
esac
''')
        self.write("bin/sleep", "#!/bin/sh\nexit 0\n")
        self.write("bin/sync", "#!/bin/sh\nexit 0\n")
        self.write("usr/sbin/proxy-switch", '''#!/bin/sh
echo "switch $1" >> "$TEST_ROOT/actions"
if [ "$1" = homeproxy ] && [ "${BAD_CORE:-0}" != 1 ]; then touch "$TEST_ROOT/core.running"; fi
exit 0
''')
        self.write("etc/init.d/proxy-guard", '''#!/bin/sh
echo "guard $1" >> "$TEST_ROOT/actions"
''')

    def write(self, name, data):
        path = self.root / name
        path.write_text(data, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def install(self, *args):
        command = 'export PATH="$TEST_ROOT/bin:$PATH"; exec "$@"'
        return subprocess.run([SH, "-c", command, "installer", posix(self.root / "install.sh"), *args],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def test_schema_failure_keeps_live_config_and_services(self):
        self.env["BAD_SCHEMA"] = "1"
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "etc/config/homeproxy").read_text(), "original homeproxy\n")
        self.assertFalse((self.root / "actions").exists())

    def test_check_only_does_not_activate(self):
        result = self.install("--check-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "etc/config/homeproxy").read_text(), "original homeproxy\n")
        self.assertFalse((self.root / "actions").exists())

    def test_success_activates_validated_profile_and_resumes_guard(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "etc/config/homeproxy").read_text(), "new mainland profile\n")
        self.assertEqual((self.root / "actions").read_text().splitlines(),
                         ["guard stop", "switch homeproxy", "guard start"])

    def test_failed_core_restores_config_and_previous_frontend(self):
        self.env["BAD_CORE"] = "1"
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        for name in ("homeproxy", "passwall", "proxy-guard"):
            self.assertEqual((self.root / "etc/config" / name).read_text(), "original " + name + "\n")
        self.assertEqual((self.root / "actions").read_text().splitlines(),
                         ["guard stop", "switch homeproxy", "switch passwall", "guard start"])
