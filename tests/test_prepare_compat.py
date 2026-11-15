"""Build-time source integrity and manifest drift checks."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare", ROOT / "scripts/prepare_compat.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class CompatibilityBuildTests(unittest.TestCase):
    def test_init_patch_preserves_native_start_and_removes_no_routes(self):
        source = '#!/bin/sh\nlog() {\n echo test\n}\nstart_service() {\n native_start\n}\nservice_stopped() {\n cleanup\n}\n'
        result = prepare.patch_init(source)
        self.assertIn('mkdir -p "$RUN_DIR"', result)
        self.assertIn('native_start', result)
        self.assertIn('\treturn 0\n}', result)
        self.assertNotIn('network.interface.wan', result)
        self.assertNotIn('ip route show default', result)

    def test_changed_init_layout_fails_closed(self):
        with self.assertRaises(ValueError):
            prepare.patch_init("a changed init script")

    def test_bad_source_digest_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "source"
            with patch.object(prepare.urllib.request, "urlopen", return_value=io.BytesIO(b"modified")):
                with self.assertRaises(ValueError):
                    prepare.verified_source(dict(url="https://example.invalid/source", sha256="0" * 64), cache)
            self.assertFalse(cache.exists())

    def test_cached_source_must_match_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "source"
            cache.write_bytes(b"verified source")
            result = prepare.verified_source(dict(url="unused", sha256=hashlib.sha256(cache.read_bytes()).hexdigest()), cache)
            self.assertEqual(result, "verified source")

    def test_manifest_drift_blocks_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.manifest"
            path.write_text("luci-app-homeproxy - 26.298.29275~c84adb9\nsing-box - 1.12.25-r1\n")
            prepare.check_manifest(path)
            path.write_text("luci-app-homeproxy - 26.298.29275~c84adb9\nsing-box - changed\n")
            with self.assertRaises(ValueError):
                prepare.check_manifest(path)
            path.write_text("luci-app-homeproxy - changed\nsing-box - 1.14.4-r1\n")
            with self.assertRaises(ValueError):
                prepare.check_manifest(path)

    def test_missing_homeproxy_manifest_does_not_need_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.manifest"
            path.write_text("base-files - fixture\n")
            prepare.check_manifest(path)
