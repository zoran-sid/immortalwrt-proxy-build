#!/usr/bin/env python3
"""Regression tests for HomeProxy boot persistence / fail-open semantics."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class FailOpenPreservesChoiceTests(unittest.TestCase):
    def test_watchdog_stop_keeps_enable_state(self):
        text = read("modules", "proxy-guard", "files", "usr", "sbin",
                    "proxy-guard-watchdog")
        # Fail-open may stop the runtime, but must not erase the user's choice:
        # --keep-enabled keeps homeproxy.config.enabled and /etc/rc.d/S99*.
        self.assertIn("--keep-enabled", text)
        self.assertNotIn("proxy-switch off --keep-record >", text)

    def test_proxy_switch_has_keep_enabled_and_ensure(self):
        text = read("modules", "proxy-guard", "files", "usr", "sbin", "proxy-switch")
        for flag in ("--keep-enabled", "--ensure", "--no-start", "--keep-record"):
            self.assertIn(flag, text)
        # keep-enabled must skip both the UCI flag write and the rc.d disable.
        self.assertIn('[ "$KEEP_ENABLED" -eq 1 ] || set_enabled "$s" 0', text)
        self.assertIn('[ "$KEEP_ENABLED" -eq 1 ] || /etc/init.d/"$s" disable', text)


class BootReconcileTests(unittest.TestCase):
    def test_reconcile_handles_ui_enable_and_no_node(self):
        text = read("modules", "proxy-guard", "files", "usr", "sbin",
                    "proxy-guard-reconcile")
        self.assertIn("homeproxy_configured", text)
        self.assertIn("main_node", text)
        self.assertIn("no node/server configured", text)
        self.assertIn("--ensure", text)
        self.assertIn("--no-start", text)
        self.assertIn("failopen", text)

    def test_proxy_guard_init_reconciles_before_watchdog(self):
        text = read("modules", "proxy-guard", "files", "etc", "init.d", "proxy-guard")
        self.assertIn("proxy-guard-reconcile --boot", text)
        self.assertIn("/var/run/homeproxy", text)

    def test_wan_hotplug_is_event_driven(self):
        text = read("modules", "proxy-guard", "files", "etc", "hotplug.d",
                    "iface", "99-proxy-guard")
        self.assertIn('ifup', text)
        self.assertIn("*wan*", text)
        self.assertIn("--event", text)
        # No fixed sleeps as a readiness workaround.
        self.assertNotIn("sleep", text)


if __name__ == "__main__":
    unittest.main()
