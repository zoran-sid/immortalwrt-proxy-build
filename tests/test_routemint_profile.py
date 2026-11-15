"""Policy semantics and packaged bytes for the staged HomeProxy importer."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "scripts/routemint/cn-mainland.json").read_text())
PROGRAM = ROOT / "scripts/routemint/configure.uc"


@unittest.skipUnless(shutil.which("node"), "Node required for the ucode-compatible policy logic")
class PolicyTests(unittest.TestCase):
    def base(self):
        return {
            "config": {".type": "homeproxy", "main_node": "hk", "routing_mode": "bypass_mainland_china"},
            "hk": {".type": "node", "type": "vmess", "uuid": "fixture-secret", "address": "fixture.invalid"},
            "jp": {".type": "node", "type": "vmess", "uuid": "second-fixture"},
            "subscription": {".type": "homeproxy", "url": "https://fixture.invalid/secret"},
            "old_rule": {".type": "routing_rule", "enabled": "1", "outbound": "old_node"},
        }

    def configure(self, state, env=None, success=True):
        code = "\n".join(line for line in PROGRAM.read_text(encoding="utf-8").splitlines()
                         if not line.startswith("import "))
        shim = r'''
const input = JSON.parse(require('fs').readFileSync(0,'utf8'));
let state = input.state, writes = {}, logs = [], cursorArgs;
const getenv = key => ({RM_STAGE:'/isolated',RM_PLAN:'/plan',...input.env})[key];
const json = JSON.parse, length = x => x.length;
const type = x => Array.isArray(x) ? 'array' : typeof x;
const index = (a,v) => a.indexOf(v), push = (a,v) => a.push(v);
const split = (s,sep) => s.split(sep), join = (sep,a) => a.join(sep);
const uc = s => s.toUpperCase();
const print = (...args) => logs.push(args.join(''));
const die = msg => { throw new Error(msg); };
const readfile = path => path === '/plan' ? JSON.stringify(input.plan) :
 (input.env.TEST_NAMED_ROUTES ? '// proxy-guard: unique tags per routing node\n' : '') +
 "const uci = cursor();\nwritefile(RUN_DIR + '/sing-box-c.json', 'fixture');";
const writefile = (path,data) => { writes[path] = data; return true; };
const cursor = (...args) => {
 cursorArgs=args;
 return {
  load: () => true,
  get: (conf,s,o) => o === undefined ? state[s]?.['.type'] : state[s]?.[o],
  set: function(conf,s,o,v) {
   if (arguments.length === 3) state[s]={'.type':o};
   else { if (!state[s]) return null; state[s][o]=v; }
   return true;
  },
  foreach: (conf,kind,fn) => Object.entries(state).filter(([n,v])=>!kind||v['.type']===kind)
    .forEach(([n,v])=>fn({...v,'.name':n})),
  delete: (conf,s,o) => { if(o === undefined) delete state[s]; else if(state[s]) delete state[s][o]; return true; },
  commit: () => true
 };
};
'''
        tail = "\nconsole.log(JSON.stringify({state,writes,logs,cursorArgs}));"
        result = subprocess.run(["node", "-e", shim + code + tail],
                                input=json.dumps(dict(state=state, plan=PLAN, env=env or {})),
                                capture_output=True, text=True)
        if not success:
            self.assertNotEqual(result.returncode, 0)
            return result.stderr
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_preserves_nodes_and_subscription_and_disables_old_custom_rules(self):
        before = self.base()
        result = self.configure(before)
        after = result["state"]
        for key in ("hk", "jp", "subscription"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["old_rule"]["enabled"], "0")
        self.assertEqual(after["routing"]["default_outbound"], "rm_proxy")
        self.assertEqual(after["config"]["routing_mode"], "custom")
        self.assertEqual(result["cursorArgs"], ["/isolated/config", "/isolated/delta"])
        self.assertNotIn("fixture-secret", "".join(result["logs"]))

    def test_mainland_order_and_dns_follow_service_outbounds(self):
        result = self.configure(self.base())
        state = result["state"]
        rules = [s for s in state.values() if s[".type"] == "routing_rule" and s.get("routemint") == "1"]
        categories = [s["label"].removeprefix("RouteMint ") for s in rules]
        self.assertEqual(categories, [r[0] for r in PLAN["rules"]])
        self.assertEqual(result["writes"]["/isolated/categories"].splitlines(), categories)
        self.assertEqual(len(categories), 29)
        self.assertLess(categories.index("ai-gemini"), categories.index("google"))
        self.assertLess(categories.index("game-download"), categories.index("game-platform"))
        self.assertNotIn("reject-malware", categories)
        self.assertEqual(state["rm_route_reject_ads"]["action"], "reject")
        self.assertEqual(state["rm_route_game_download"]["outbound"], "direct-out")
        self.assertEqual(state["rm_route_game_platform"]["outbound"], "rm_proxy")
        self.assertEqual(state["rm_query_netflix"]["server"], "rm_dns_netflix")
        self.assertEqual(state["rm_dns_netflix"]["outbound"], "rm_netflix")
        self.assertEqual(state["rm_dns_local"]["outbound"], "direct-out")
        self.assertNotIn("rm_query_cn_ip", state)

    def test_reuses_selected_nodes_and_accepts_ai_override(self):
        result = self.configure(self.base(), {"RM_AI_NODE": "jp"})["state"]
        self.assertEqual(result["rm_proxy"]["urltest_nodes"], ["hk"])
        self.assertEqual(result["rm_ai"]["urltest_nodes"], ["jp"])
        self.assertEqual(result["rm_netflix"]["urltest_nodes"], ["hk"])
        for name in PLAN["groups"]:
            self.assertEqual(result["rm_" + name]["node"], "urltest")

    def test_rerun_is_idempotent_and_keeps_custom_default_nodes(self):
        first = self.configure(self.base())["state"]
        second = self.configure(first)["state"]
        self.assertEqual(first, second)
        first["rm_proxy"]["urltest_nodes"] = ["jp"]
        third = self.configure(first)["state"]
        self.assertEqual(third["rm_proxy"]["urltest_nodes"], ["jp"])

    def test_missing_node_aborts_before_committing(self):
        self.assertIn("Unknown node ID", self.configure(self.base(), {"RM_AI_NODE": "missing"}, success=False))

    def test_baked_firmware_uses_concrete_nodes_for_single_member_groups(self):
        result = self.configure(self.base(), {"TEST_NAMED_ROUTES": "1"})["state"]
        for name in PLAN["groups"]:
            self.assertEqual(result["rm_" + name]["node"], "hk")
            self.assertNotIn("urltest_nodes", result["rm_" + name])

    def test_preview_uses_isolated_config_and_output(self):
        preview = self.configure(self.base())["writes"]["/isolated/generate-preview.uc"]
        self.assertIn("cursor(getenv('RM_STAGE') + '/config'", preview)
        self.assertIn("writefile(getenv('RM_STAGE') + '/preview.json'", preview)
        self.assertNotIn("writefile(RUN_DIR", preview)


class PackageTests(unittest.TestCase):
    def test_bundle_is_lf_text_with_only_expected_files(self):
        spec = importlib.util.spec_from_file_location("package_route", ROOT / "scripts/package_routemint.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            path = module.build(Path(tmp) / "rules.tar.gz")
            with tarfile.open(path) as archive:
                self.assertEqual(len(archive.getmembers()), 3)
                for member in archive.getmembers():
                    data = archive.extractfile(member).read()
                    self.assertNotIn(b"\r", data)
                    self.assertEqual(member.mode, 0o755 if member.name.endswith(".sh") else 0o644)
