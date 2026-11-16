"""Check archive bytes, permissions and execution, including CRLF checkouts."""
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_fix", ROOT / "scripts/package_homeproxy_fix.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
SH = os.environ.get("TEST_SH") or shutil.which("sh")


class RepairPackageTests(unittest.TestCase):
    def test_crlf_source_is_normalized_and_size_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "script"
            source.write_bytes(b"#!/bin/sh\r\nexit 2\r\n")
            output = root / "test.tar"
            with tarfile.open(output, "w") as archive:
                package.add(archive, source, "script", True)
            with tarfile.open(output) as archive:
                data = archive.extractfile("script").read()
                self.assertEqual(data, b"#!/bin/sh\nexit 2\n")
                self.assertEqual(archive.getmember("script").size, len(data))
                self.assertEqual(archive.getmember("script").mode, 0o755)

    def test_real_bundle_has_linux_newlines_and_executable_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for build, filename in ((package.build, "fix.tar.gz"),
                                    (package.build_fast_start, "fast-start.tar.gz")):
                output = build(Path(tmp) / filename)
                with tarfile.open(output) as archive:
                    for member in archive.getmembers():
                        data = archive.extractfile(member).read()
                        self.assertNotIn(b"\r", data, member.name)
                        if not member.name.endswith(".uc"):
                            self.assertTrue(data.startswith(b"#!/bin/sh\n") or
                                            data.startswith(b"#!/bin/sh /etc/rc.common\n"), member.name)
                            self.assertEqual(member.mode, 0o755, member.name)

    @unittest.skipUnless(SH, "POSIX shell required (set TEST_SH on Windows)")
    def test_archived_proxy_switch_executes_via_its_shebang(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = package.build(Path(tmp) / "fix.tar.gz")
            executable = Path(tmp) / "proxy-switch"
            with tarfile.open(output) as archive:
                executable.write_bytes(archive.extractfile(
                    "homeproxy-fix/rootfs/usr/sbin/proxy-switch").read())
            executable.chmod(0o755)
            path = executable.as_posix()
            if os.name == "nt":
                path = "/" + path[0].lower() + path[2:]
            # Execute the archived file itself; `sh file` bypasses its shebang
            # and would miss the router's '/bin/sh\r: not found' failure.
            result = subprocess.run([SH, "-c", shlex.quote(path) + " invalid-frontend"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("usage: proxy-switch", result.stderr)
