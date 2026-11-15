"""Exercise the migration's JS-compatible ucode subset on representative JSON.

TEST_SING_BOX optionally points to a real sing-box binary for schema checks.
This checks the transform; the installer also compiles ucode on the router.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / "modules/proxy-homeproxy/files/usr/share/homeproxy/compat-1.14.uc"


@unittest.skipUnless(shutil.which("node"), "Node required for migration tests")
class CompatibilityTests(unittest.TestCase):
    def migrate(self, config):
        runner = '''const fs = require('fs');
let config = JSON.parse(fs.readFileSync(0, 'utf8'));
const length = x => x.length;
const push = (x, v) => x.push(v);
const filter = (x, f) => x.filter(f);
'''
        runner += SNIPPET.read_text(encoding="utf-8")
        runner += "\nconsole.log(JSON.stringify(config, (k,v) => v === null ? undefined : v));"
        result = subprocess.run(["node", "-e", runner], input=json.dumps(config),
                                text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def fixture(self):
        return {
            "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1",
                          "listen_port": 15330, "sniff": True,
                          "sniff_override_destination": True}],
            "outbounds": [{"type": "direct", "tag": "direct-out"},
                          {"type": "block", "tag": "block-out"},
                          {"type": "socks", "tag": "main-out", "server": "127.0.0.1",
                           "server_port": 1080, "username": "fixture", "password": "fixture"}],
            "route": {"final": "main-out", "rules": [
                {"domain": ["blocked.invalid"], "outbound": "block-out"}]},
            "dns": {"servers": [{"type": "local", "tag": "local"}], "independent_cache": True},
            "experimental": {"cache_file": {"enabled": True, "store_rdrc": True,
                                             "rdrc_timeout": "1d"}},
        }

    def test_migrates_inbound_block_and_removed_cache_options(self):
        config = self.fixture()
        result = self.migrate(config)
        self.assertNotIn("sniff", result["inbounds"][0])
        self.assertEqual(result["route"]["rules"][0], {"inbound": ["mixed-in"], "action": "sniff"})
        self.assertEqual(result["route"]["rules"][1], {"domain": ["blocked.invalid"], "action": "reject"})
        self.assertEqual(result["outbounds"][-1], config["outbounds"][-1])
        self.assertEqual(result["experimental"]["cache_file"], {"enabled": True})
        self.assertNotIn("independent_cache", result["dns"])
        self.assertEqual(self.migrate(result), result)

    def test_custom_block_final_stays_rejected(self):
        config = self.fixture()
        config["route"]["final"] = "block-out"
        result = self.migrate(config)
        self.assertNotIn("final", result["route"])
        self.assertEqual(result["route"]["rules"][-1], {"action": "reject"})

    @unittest.skipUnless(os.environ.get("TEST_SING_BOX"), "real sing-box not configured")
    def test_real_core_accepts_migration_and_checks_original_by_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(self.fixture()), encoding="utf-8")
            command = [os.environ["TEST_SING_BOX"], "check", "-c", str(path)]
            original = subprocess.run(command, capture_output=True, text=True)
            version = subprocess.run([os.environ["TEST_SING_BOX"], "version"],
                                     capture_output=True, text=True, check=True).stdout
            match = re.search(r"version (\d+)\.(\d+)\.", version)
            self.assertIsNotNone(match, version)
            if tuple(map(int, match.groups())) >= (1, 13):
                self.assertNotEqual(original.returncode, 0)
                self.assertIn("legacy inbound", original.stderr)
            else:
                self.assertEqual(original.returncode, 0, original.stderr)
            path.write_text(json.dumps(self.migrate(self.fixture())), encoding="utf-8")
            fixed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
