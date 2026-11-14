#!/usr/bin/env python3
"""Contract tests between scripts/resolve.py, scripts/build.sh and the presets.

Run: python3 tests/test_build_plan.py -v

These tests do not build an image. They catch the class of failure where the
resolver and build.sh disagree about the plan (run 43551668831 failed after the
image had been built because PRESET_KIND was never emitted), and where a preset
references a blocked module (full.yml still listed proxy-nikki).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOLVE = os.path.join(ROOT, "scripts", "resolve.py")

# Emitted by resolve.py::emit_env and consumed by scripts/build.sh.
REQUIRED_ENV = [
    "TARGET_NAME",
    "TARGET",
    "SUBTARGET",
    "PROFILE",
    "ARCH",
    "TARGET_KIND",
    "ROOTFS_PARTSIZE",
    "PRESET_NAME",
    "PRESET_KIND",
    "PRESET_PROXY",
    "MODULE_LIST",
    "PACKAGES",
]


def run(*args):
    return subprocess.run(
        [sys.executable, RESOLVE] + list(args),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def build_env(target, preset, modules=""):
    """Return the plan environment exactly as build.sh sources it."""
    args = ["build", "--target", target, "--preset", preset, "--format", "json"]
    if modules:
        args += ["--modules", modules]
    res = run(*args)
    if res.returncode != 0:
        raise AssertionError("resolve failed: %s" % (res.stderr or res.stdout))

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "plan.env")
        args = [
            "build",
            "--target",
            target,
            "--preset",
            preset,
            "--format",
            "env",
            "--out",
            out,
        ]
        if modules:
            args += ["--modules", modules]
        res = run(*args)
        if res.returncode != 0:
            raise AssertionError("resolve env failed: %s" % res.stderr)
        with open(out, encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    env = {}
    for line in lines:
        key, _, value = line.partition("=")
        # emit_env single-quotes every value and escapes embedded quotes.
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("'\\''", "'")
        env[key] = value
    return env


class PresetTests(unittest.TestCase):
    def test_full_preset_resolves_for_every_listed_target(self):
        # Regression: full.yml kept proxy-nikki after it became blocked, so the
        # full preset (and therefore the tag matrix) could not resolve at all.
        path = os.path.join(ROOT, "presets", "full.yml")
        with open(path, encoding="utf-8") as fh:
            spec = yaml.safe_load(fh)
        for target in spec["targets"]:
            res = run("check", "--target", target, "--preset", "full")
            self.assertEqual(
                res.returncode, 0, "%s/full: %s" % (target, res.stderr.strip())
            )

    def test_no_preset_references_a_blocked_module(self):
        blocked = set()
        mods_dir = os.path.join(ROOT, "modules")
        for name in os.listdir(mods_dir):
            path = os.path.join(mods_dir, name, "module.yml")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                mod = yaml.safe_load(fh) or {}
            if mod.get("status") == "blocked":
                blocked.add(name)
        self.assertTrue(blocked, "expected at least one blocked module")

        presets_dir = os.path.join(ROOT, "presets")
        offenders = []
        for name in os.listdir(presets_dir):
            if not name.endswith((".yml", ".yaml")):
                continue
            with open(os.path.join(presets_dir, name), encoding="utf-8") as fh:
                spec = yaml.safe_load(fh) or {}
            for mod in spec.get("modules") or []:
                if mod in blocked:
                    offenders.append("%s -> %s" % (name, mod))
        self.assertEqual(offenders, [], "blocked modules in presets: %s" % offenders)


class MatrixTests(unittest.TestCase):
    def test_tag_matrix_rows_resolve(self):
        res = run("matrix", "--event", "tag")
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = json.loads(res.stdout)["include"]
        self.assertTrue(rows)
        for row in rows:
            args = ["check", "--target", row["target"], "--preset", row["preset"]]
            res = run(*args)
            self.assertEqual(
                res.returncode,
                0,
                "%s/%s: %s"
                % (row["target"], row["preset"], (res.stderr or res.stdout).strip()),
            )

    def test_dispatch_matrix_resolves_for_custom_modules(self):
        res = run(
            "matrix", "--event", "dispatch", "--target", "x86-64",
            "--preset", "custom", "--modules", "base,nat,proxy-homeproxy",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = json.loads(res.stdout)["include"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["preset"].startswith("custom-"))


class EnvContractTests(unittest.TestCase):
    def test_standard_env_has_every_required_key(self):
        env = build_env("x86-64", "standard")
        for key in REQUIRED_ENV:
            self.assertIn(key, env, "resolve.py did not emit %s" % key)
        self.assertEqual(env["PRESET_KIND"], "preset")
        self.assertEqual(env["PRESET_NAME"], "standard")

    def test_rescue_env_allows_empty_proxy(self):
        env = build_env("x86-64", "rescue")
        for key in REQUIRED_ENV:
            self.assertIn(key, env)
        # rescue has no proxy frontend; empty is valid but the key must exist.
        self.assertEqual(env["PRESET_PROXY"], "")

    def test_custom_env_reports_custom_kind(self):
        env = build_env("x86-64", "custom", "base,nat")
        self.assertEqual(env["PRESET_KIND"], "custom")
        self.assertRegex(env["PRESET_NAME"], r"^custom-[0-9a-f]{8}$")

    def test_env_covers_every_preset_in_the_tag_matrix(self):
        res = run("matrix", "--event", "tag")
        rows = json.loads(res.stdout)["include"]
        seen = set()
        for row in rows:
            pair = (row["target"], row["preset"])
            if pair in seen:
                continue
            seen.add(pair)
            env = build_env(*pair)
            for key in REQUIRED_ENV:
                self.assertIn(key, env, "%s/%s missing %s" % (pair[0], pair[1], key))


class SecurityInvariantTests(unittest.TestCase):
    def test_build_does_not_disable_apk_signature_checks(self):
        # The unsigned third-party index workaround used to override the
        # ImageBuilder's signature setting for the whole build. Unsigned package
        # repositories must stay unsupported rather than silently trusted.
        with open(os.path.join(ROOT, "scripts", "build.sh"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("CONFIG_SIGNATURE_CHECK", text)


if __name__ == "__main__":
    unittest.main()
