#!/usr/bin/env python3
"""Static regression tests for the on-device shell/UI code.

Run: python3 tests/test_module_scripts.py -v

These do not need a router. They guard the on-device scripts against the
mistakes that are easy to reintroduce and expensive to find in a live router:
BusyBox-ash incompatibilities, secret-leaking diagnostics, blocked frontends in
the wizard, volatile rollback backups and a fail-open watchdog that can never
recover.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def module_shell_files():
    """Every shell script shipped in modules/ (init.d, uci-defaults, *.sh)."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, "modules")):
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT)
            if name.endswith(".sh") or "/init.d/" in rel.replace("\\", "/") \
                    or "uci-defaults" in rel.replace("\\", "/"):
                out.append(path)
    return sorted(out)


class BusyBoxCompatTests(unittest.TestCase):
    def test_no_here_strings_in_on_device_scripts(self):
        # BusyBox ash (the /bin/sh of OpenWrt) has no <<< here-strings. The old
        # network-doctor used them, so classification silently misbehaved.
        offenders = []
        for path in module_shell_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if " <<<" in text:
                offenders.append(os.path.relpath(path, ROOT))
        if " <<<" in read("scripts", "diagnostics", "network-doctor.sh"):
            offenders.append("scripts/diagnostics/network-doctor.sh")
        self.assertEqual(offenders, [], "here-strings are not BusyBox-ash compatible")

    def test_nounset_scripts_survive_functions_sh(self):
        # /lib/functions.sh dereferences IPKG_INSTROOT while being sourced. With
        # `set -u` that aborts the daemon at startup (the proxy-guard watchdog
        # died this way and the guard silently stopped working).
        offenders = []
        for path in module_shell_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if "set -u" in text and ". /lib/functions.sh" in text:
                if "IPKG_INSTROOT" not in text:
                    offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [], "set -u + functions.sh needs IPKG_INSTROOT")

    def test_module_scripts_avoid_bash_arrays(self):
        import re

        offenders = []
        pattern = re.compile(r"(?<!\$)\(\(|\[\s*\[|<<<")
        for path in module_shell_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if "#!/bin/bash" in text:
                continue
            if pattern.search(text):
                offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [], "bash-only syntax in /bin/sh scripts")


class DiagnosticsTests(unittest.TestCase):
    def test_redact_node_ip_adds_to_the_secret_filter(self):
        # --redact-node-ip used to replace redact(), which then let subscription
        # URLs, UUIDs and tokens through. It must compose.
        text = read("scripts", "diagnostics", "network-doctor.sh")
        self.assertIn("redact_secrets | redact_node_ip", text)
        self.assertIn("vmess|vless|trojan", text)
        self.assertIn("private[-_]?key", text)

    def test_classification_separates_dns_from_egress(self):
        text = read("scripts", "diagnostics", "network-doctor.sh")
        self.assertIn("DNS_FOREIGN_FAILURE", text)
        self.assertIn("NODE_FAILURE", text)
        self.assertIn("INTERNET_DOWN", text)
        # Firewall rules only matter while a proxy is actually active.
        self.assertIn("proxy_active", text)


class WizardTests(unittest.TestCase):
    def test_blocked_frontend_is_not_offered(self):
        text = read("modules", "wizard", "files", "www", "luci-static",
                    "resources", "view", "wizard", "wizard.js")
        self.assertNotIn("'value': 'nikki'", text)

    def test_rollback_backup_survives_reboot(self):
        text = read("modules", "wizard", "files", "usr", "libexec", "wizard", "apply.sh")
        self.assertIn("/etc/wizard-backups", text)
        self.assertNotIn("/tmp/wizard-backup", text)

    def test_rollback_reapplies_network_and_services(self):
        text = read("modules", "wizard", "files", "usr", "libexec", "wizard", "rollback.sh")
        for needle in ("/etc/init.d/network reload", "/etc/init.d/dnsmasq restart",
                       "/etc/init.d/firewall reload", "proxy-switch"):
            self.assertIn(needle, text)

    def test_apply_checks_frontend_and_validates_pppoe(self):
        text = read("modules", "wizard", "files", "usr", "libexec", "wizard", "apply.sh")
        self.assertIn("is not installed", text)
        self.assertIn("PPPoE scenario needs wan_user", text)
        self.assertIn("wizard-apply.lock", text)


class ProxyGuardTests(unittest.TestCase):
    def test_watchdog_uses_multiple_probes(self):
        text = read("modules", "proxy-guard", "files", "usr", "sbin",
                    "proxy-guard-watchdog")
        # DNS list and a separate CN baseline keep one unreachable probe from
        # triggering fail-open in a different network (CN vs HK vs travel).
        self.assertIn("probe_dns", text)
        self.assertIn("probe_cn", text)
        self.assertIn("baseline_ok", text)

    def test_watchdog_recovers_after_fail_open(self):
        text = read("modules", "proxy-guard", "files", "usr", "sbin",
                    "proxy-guard-watchdog")
        # Recovery must re-enable the proxy instead of waiting for the foreign
        # probe to succeed while the proxy is off (the old deadlock).
        self.assertIn("recovery attempt", text)
        self.assertIn("recover_max", text)
        self.assertIn("next_recover", text)

    def test_switch_restores_previous_frontend_on_failure(self):
        text = read("modules", "proxy-guard", "files", "usr", "sbin", "proxy-switch")
        self.assertIn("restoring previous frontend", text)
        self.assertIn("is not installed; nothing changed", text)


if __name__ == "__main__":
    unittest.main()
