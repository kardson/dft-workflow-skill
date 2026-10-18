import hashlib
import json
from pathlib import Path
import unittest
from test_environment_check import runtime_directory
from task_resume import resume

class ResumeTests(unittest.TestCase):
    def fixture(self, root):
        evidence = root / "startup.json"; evidence.write_text("historical startup", encoding="utf-8")
        value = {"schema": "dft-continuation/v1", "task_id": "task", "stage": "RUNNING", "writer": "executor", "evidence": [{"path": evidence.name, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
        path = root / "continue.json"; path.write_text(json.dumps(value), encoding="utf-8")
        return path, evidence, value
    def test_running_is_historical_and_never_resubmitted(self):
        with runtime_directory() as temp:
            path, evidence, value = self.fixture(Path(temp))
            result = resume(path, temp)
            self.assertEqual(result["next_action"], "QUERY_STATUS_ON_REQUEST")
            self.assertFalse(result["execution_authorized"])
            self.assertEqual(evidence.read_text(), "historical startup")
    def test_changed_and_missing_sources_block(self):
        with runtime_directory() as temp:
            path, evidence, value = self.fixture(Path(temp))
            evidence.write_text("changed")
            self.assertEqual(resume(path, temp)["status"], "BLOCKED")
            evidence.unlink()
            self.assertTrue(resume(path, temp)["problems"][0].startswith("MISSING:"))
    def test_escape_and_unknown_stage_rejected(self):
        with runtime_directory() as temp:
            path, evidence, value = self.fixture(Path(temp))
            value["evidence"][0]["path"] = "../../outside"
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): resume(path, temp)
            value["stage"] = "GUESS"
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): resume(path, temp)
