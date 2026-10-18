import unittest
import subprocess
import sys
import json
from pathlib import Path
from test_environment_check import runtime_directory
import environment_check as env
from project_setup import summarize, brief

class SetupTests(unittest.TestCase):
    def reports(self):
        return [env.report(scope, [env.item(name, "PASS", {"source": "synthetic"}) for name in names])
                for scope, names in (("tools", env.TOOLS_IDS), ("workstation", env.WORKSTATION_IDS))]

    def test_absent_reports_never_ready(self):
        value = summarize(None, None, "single", {})
        self.assertEqual(value["status"], "ACTION_REQUIRED")
        self.assertEqual(len(value["issues"]), len(env.TOOLS_IDS) + len(env.WORKSTATION_IDS))
        self.assertFalse(value["execution_authorized"])
        self.assertIn("VERIFY", brief(value))

    def test_mode_needs_observation_not_configuration(self):
        reports = self.reports()
        for mode, caps in (("subagent", {"spawn": {"state": "PASS"}}), ("paired", {})):
            self.assertEqual(summarize(*reports, mode, caps)["status"], "ACTION_REQUIRED")
        value = summarize(*reports, "subagent", {"spawn": {"state": "PASS", "basis": "Synthetic successful host probe"}})
        self.assertEqual(value["status"], "READY_FOR_PLAN_DISCUSSION")
        self.assertFalse(value["execution_authorized"])

    def test_invalid_environment_not_silently_accepted(self):
        tools, workstation = self.reports()
        tools["checks"].pop()
        with self.assertRaises(ValueError):
            summarize(tools, workstation, "single", {})

    def test_empty_capability_basis_is_not_evidence(self):
        for basis in (None, False, {}, [], 0):
            result=summarize(*self.reports(), "subagent", {"spawn":{"state":"PASS","basis":basis}})
            self.assertEqual(result["status"],"ACTION_REQUIRED")

    def test_cli_creates_actionable_issue_delivery(self):
        with runtime_directory() as temp:
            output=Path(temp)/'setup'
            command=[sys.executable,'-B',str(Path(__file__).with_name('project_setup.py')),'--mode','single','--output-dir',str(output)]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,1,result.stderr)
            issues=json.loads((output/'issues.json').read_text(encoding='utf-8'))
            self.assertTrue(issues['issues'])
            self.assertTrue(all(row['impact'] and row['user_decision'] for row in issues['issues']))
            self.assertIn('Agent 下一步',(output/'ISSUES.md').read_text(encoding='utf-8'))
            original=(output/'setup.json').read_bytes()
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            self.assertEqual((output/'setup.json').read_bytes(),original)
