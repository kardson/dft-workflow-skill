import unittest
from issue_report import render

class IssueTests(unittest.TestCase):
    def value(self):
        return {"schema": "dft-issue-report/v1", "task_id": "example", "status": "PARTIAL", "issues": [{"reason": "OUTCAR missing", "impact": "Cannot verify convergence", "agent_next_action": "Inspect approved recovered file list", "user_decision": "None yet", "evidence_refs": []}]}
    def test_partial_delivery_does_not_invent_evidence(self):
        self.assertIn("尚无", render(self.value()))
    def test_missing_remedy_or_impact_rejected(self):
        for field in ("reason", "impact", "agent_next_action", "user_decision"):
            value = self.value(); del value["issues"][0][field]
            with self.assertRaises(ValueError): render(value)
    def test_success_not_delivered_as_failure(self):
        value = self.value(); value["status"] = "PASS"
        with self.assertRaises(ValueError): render(value)

    def test_sidecar_preserves_original_evidence_and_refuses_overwrite(self):
        from issue_report import write_issue
        from test_environment_check import runtime_directory
        from pathlib import Path
        with runtime_directory() as temp:
            root=Path(temp);evidence=root/'facts.json';evidence.write_text('original')
            write_issue(root,'task','FAILED',['backend failed'],'no result','inspect environment','none',['facts.json'])
            self.assertIn('backend failed',(root/'ISSUES.md').read_text(encoding='utf-8'))
            self.assertEqual(evidence.read_text(),'original')
            with self.assertRaises(FileExistsError):
                write_issue(root,'task','FAILED',['changed'],'no result','inspect','none',['facts.json'])
