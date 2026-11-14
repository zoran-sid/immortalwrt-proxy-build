#!/usr/bin/env python3
"""Unit tests for scripts/resolve.py. Run: python3 tests/test_resolve.py -v"""

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOLVE = os.path.join(ROOT, "scripts", "resolve.py")


def run(*args):
    return subprocess.run(
        [sys.executable, RESOLVE] + list(args),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def plan(target, preset, modules=""):
    args = ["build", "--target", target, "--preset", preset, "--format", "json"]
    if modules:
        args += ["--modules", modules]
    res = run(*args)
    if res.returncode != 0:
        raise AssertionError("resolve failed: %s" % (res.stderr or res.stdout))
    return json.loads(res.stdout)


class ResolverTests(unittest.TestCase):
    def test_conflicting_dns_modules_are_rejected(self):
        res = run("check", "--target", "x86-64", "--preset", "custom",
                  "--modules", "base,dns-adguardhome,dns-smartdns")
        self.assertEqual(res.returncode, 2)
        self.assertIn("conflict", res.stderr)

    def test_module_bound_to_other_target_is_rejected(self):
        res = run("check", "--target", "x86-64", "--preset", "custom",
                  "--modules", "base,vm-guest")
        self.assertEqual(res.returncode, 2)
        self.assertIn("not available for target", res.stderr)

    def test_preset_not_applicable_to_target(self):
        res = run("check", "--target", "gl-mt2500", "--preset", "full")
        self.assertEqual(res.returncode, 2)
        self.assertIn("does not apply", res.stderr)

    def test_pending_upstream_target_cannot_build(self):
        res = run("check", "--target", "brume3", "--preset", "rescue")
        self.assertEqual(res.returncode, 2)
        self.assertIn("pending-upstream", res.stderr)

    def test_unknown_module_is_rejected(self):
        res = run("check", "--target", "x86-64", "--preset", "custom",
                  "--modules", "base,does-not-exist")
        self.assertEqual(res.returncode, 2)
        self.assertIn("unknown module", res.stderr)

    def test_custom_hash_is_order_independent(self):
        one = plan("x86-64", "custom", "base,nat,proxy-homeproxy")
        two = plan("x86-64", "custom", "proxy-homeproxy,nat,base")
        self.assertEqual(one["preset_name"], two["preset_name"])
        self.assertTrue(one["preset_name"].startswith("custom-"))
        self.assertEqual(len(one["preset_name"]), len("custom-") + 8)

    def test_implicit_vm_guest_on_vm_target(self):
        p = plan("x86-64-vm", "standard")
        self.assertIn("vm-guest", p["modules"])

    def test_implicit_module_not_on_baremetal(self):
        p = plan("x86-64", "standard")
        self.assertNotIn("vm-guest", p["modules"])

    def test_guard_added_when_proxy_selected(self):
        p = plan("x86-64", "custom", "base,proxy-homeproxy")
        self.assertIn("proxy-guard", p["modules"])

    def test_requires_are_pulled_in(self):
        p = plan("x86-64", "custom", "base,monitor-pro")
        self.assertIn("monitor-lite", p["modules"])
        self.assertLess(p["modules"].index("monitor-lite"), p["modules"].index("monitor-pro"))

    def test_thirdparty_module_resolves_with_lock(self):
        p = plan("x86-64", "custom", "base,proxy-nikki")
        names = [e["name"] for e in p["thirdparty_repos"]]
        self.assertIn("nikki", names)
        self.assertTrue(p["packages"])

    def test_standard_preset_contains_expected_proxy_default(self):
        self.assertEqual(plan("x86-64", "standard")["proxy_default"], "passwall")
        self.assertEqual(plan("gl-mt2500", "lite")["proxy_default"], "homeproxy")

    def test_tag_matrix_covers_devices(self):
        res = run("matrix", "--event", "tag")
        self.assertEqual(res.returncode, 0)
        rows = json.loads(res.stdout)["include"]
        pairs = {(r["target"], r["preset"]) for r in rows}
        for expected in [
            ("x86-64", "standard"),
            ("x86-64", "full"),
            ("x86-64", "rescue"),
            ("x86-64-vm", "standard"),
            ("gl-mt2500", "lite"),
            ("gl-mt2500-airoha", "lite"),
            ("nanopi-r5s", "lite"),
            ("nanopi-r6s", "standard"),
            ("nanopi-r76s", "standard"),
        ]:
            self.assertIn(expected, pairs)
        self.assertNotIn("brume3", {t for t, _ in pairs})
        self.assertNotIn(("x86-64-vm", "full"), pairs)
        self.assertEqual({p for t, p in pairs if t == "x86-64-vm"}, {"standard"})
        self.assertIn(("nanopi-r6s", "rescue"), pairs)


if __name__ == "__main__":
    unittest.main()
