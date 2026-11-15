#!/usr/bin/env python3
"""Ensure installed LuCI apps ship the zh-cn language pack where upstream has one."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Upstream has no luci-i18n-*-zh-cn package for these (verified against the
# 25.12.2 x86_64 and both aarch64 feeds on 2026-11-15):
#   adguardhome: English-only LuCI app
#   openclash:   ships its own Chinese strings, no separate i18n package
NO_UPSTREAM_I18N = {"adguardhome", "openclash", "base"}


def module_packages():
    out = []
    base = os.path.join(ROOT, "modules")
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "packages.txt")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        out.append(line)
    return out


class I18nTests(unittest.TestCase):
    def test_every_luci_app_has_its_zh_cn_pack(self):
        packages = set(module_packages())
        apps = {p[len("luci-app-"):] for p in packages if p.startswith("luci-app-")}
        missing = []
        for app in sorted(apps):
            if app in NO_UPSTREAM_I18N:
                continue
            pack = "luci-i18n-%s-zh-cn" % app
            if pack not in packages:
                missing.append("%s -> %s" % (app, pack))
        self.assertEqual(missing, [], "LuCI apps without zh-cn language pack")

    def test_base_includes_core_ui_translations(self):
        packages = set(module_packages())
        for pack in (
            "luci-i18n-base-zh-cn",
            "luci-i18n-firewall-zh-cn",
            "luci-i18n-package-manager-zh-cn",
        ):
            self.assertIn(pack, packages)

    def test_no_translation_without_its_app(self):
        # A translation package is only useful together with its app; keep the
        # mapping in the same module honest.
        packages = set(module_packages())
        apps = {p[len("luci-app-"):] for p in packages if p.startswith("luci-app-")}
        for pack in packages:
            m = re.match(r"^luci-i18n-(.+)-zh-cn$", pack)
            if not m:
                continue
            app = m.group(1)
            if app in NO_UPSTREAM_I18N:
                continue
            self.assertIn(app, apps, "%s has no luci-app-%s" % (pack, app))


if __name__ == "__main__":
    unittest.main()
