import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import shutil
import unittest
import uuid
from unittest.mock import patch

import environment_check as env


@contextmanager
def runtime_directory():
    # tempfile's Windows 0700 directory ACL is incompatible with this managed
    # workstation. Use an isolated directory inheriting the workspace ACL.
    root = Path(__file__).resolve().parent / ".test-tmp"
    root.mkdir(exist_ok=True)
    directory = root / uuid.uuid4().hex
    directory.mkdir()
    try:
        yield str(directory)
    finally:
        directory.resolve().relative_to(root.resolve())
        shutil.rmtree(directory)


class EnvironmentTests(unittest.TestCase):
    def test_missing_interpreter_reports_every_dependency(self):
        rows = env.packages(Path("missing-python-for-test"), "analysis")
        self.assertEqual({row["id"] for row in rows}, {"package:" + key for key in env.PACKAGES["analysis"]})
        self.assertTrue(all(row["state"] == "MISSING" for row in rows))

    def test_import_failure_does_not_hide_other_packages(self):
        values = {name: {"state": "PASS", "version": "test"} for name in env.PACKAGES["science"]}
        values["ase"] = {"state": "MISSING", "error_type": "ImportError"}
        def run(argv, **kwargs):
            self.assertIn("-I", argv)
            self.assertNotIn("shell", kwargs)
            return subprocess.CompletedProcess(argv, 0, "noise\nDFT_ENV_JSON:" + json.dumps(values), "")
        rows = env.packages(sys.executable, "science", runner=run)
        self.assertEqual(next(row for row in rows if row["id"] == "package:ase")["state"], "MISSING")
        self.assertEqual(next(row for row in rows if row["id"] == "package:numpy")["state"], "PASS")

    def test_timeout_is_unknown_not_missing_or_pass(self):
        def run(*args, **kwargs):
            raise subprocess.TimeoutExpired("probe", 45)
        self.assertTrue(all(row["state"] == "UNVERIFIED" for row in env.packages(sys.executable, "science", runner=run)))

    def test_library_presence_does_not_prove_hdf5_build(self):
        with runtime_directory() as directory, patch.object(env.shutil, "which", return_value=sys.executable):
            value = env.workstation_probe(paw=directory, workdir=directory)
        self.assertEqual(next(row for row in value["checks"] if row["id"] == "compiled_features")["state"], "UNVERIFIED")
        self.assertEqual(next(row for row in value["checks"] if row["id"] == "runtime_interfaces")["state"], "UNVERIFIED")
        self.assertNotEqual(value["status"], "COMPLETE")
        self.assertFalse(any(value["effects"].values()))

    def test_no_workstation_report_cannot_produce_complete(self):
        value = env.combine(None, None)
        self.assertEqual(value["status"], "UNVERIFIED")
        self.assertEqual(len(value["checks"]), len(env.TOOLS_IDS) + len(env.WORKSTATION_IDS))

    def test_report_duplicate_future_and_wrong_scope_rejected(self):
        value = env.report("tools", [env.item(key, "PASS", {"test": True}) for key in env.TOOLS_IDS])
        env.validate_report(value, "tools")
        for mutation in ("duplicate", "future", "scope"):
            broken = copy.deepcopy(value)
            if mutation == "duplicate":
                broken["checks"][-1] = broken["checks"][0]
            elif mutation == "future":
                broken["observed_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            else:
                broken["scope"] = "workstation"
            with self.assertRaises(ValueError):
                env.validate_report(broken, "tools")

    def make_review(self):
        value = env.report("tools", [env.item(key, "UNVERIFIED", {"test": True}) for key in env.TOOLS_IDS])
        review = {"schema": "dft-environment-review/v1", "report_host": value["host"],
                  "report_observed_at": value["observed_at"], "reviewed_by": "synthetic-reviewer",
                  "reviewed_at": env.now(), "checks": [{"id": "tool_interfaces", "state": "PASS",
                   "basis": "synthetic evidence for contract test only", "evidence_refs": ["receipt.json"]}]}
        return value, review

    def test_review_is_bound_to_report_and_existing_evidence(self):
        value, review = self.make_review()
        value["platform"] = "synthetic-workstation-platform"
        with runtime_directory() as directory:
            with self.assertRaises(ValueError):
                env.apply_review(value, review, directory)
            (Path(directory) / "receipt.json").write_text("{}", encoding="utf-8")
            updated = env.apply_review(value, review, directory)
            self.assertEqual(updated["observed_at"], value["observed_at"])
            self.assertEqual(updated["platform"], "synthetic-workstation-platform")
            self.assertEqual(next(row for row in updated["checks"] if row["id"] == "tool_interfaces")["state"], "PASS")
            self.assertEqual(updated["status"], "UNVERIFIED")
            review["report_host"] = "another-host"
            with self.assertRaises(ValueError):
                env.apply_review(value, review, directory)

    def test_review_cannot_hide_missing_software_or_escape_evidence_root(self):
        value, review = self.make_review()
        with runtime_directory() as directory:
            (Path(directory) / "receipt.json").write_text("{}", encoding="utf-8")
            value["checks"][-1]["state"] = "MISSING"
            with self.assertRaises(ValueError):
                env.apply_review(value, review, directory)
            value["checks"][-1]["state"] = "UNVERIFIED"
            review["checks"][0]["evidence_refs"] = ["../outside.json"]
            with self.assertRaises(ValueError):
                env.apply_review(value, review, directory)

    def test_combined_status_uses_checks_not_claimed_status(self):
        tools = env.report("tools", [env.item(key, "PASS", {"test": True}) for key in env.TOOLS_IDS])
        host = env.report("workstation", [env.item(key, "PASS", {"test": True}) for key in env.WORKSTATION_IDS])
        host["checks"][0]["state"] = "MISSING"
        self.assertEqual(env.combine(tools, host)["status"], "MISSING")
        host["checks"][0]["state"] = "PASS"
        self.assertEqual(env.combine(tools, host)["status"], "COMPLETE")

    def test_cli_preserves_previous_output(self):
        with runtime_directory() as directory:
            marker = Path(directory) / "keep.txt"
            marker.write_text("original", encoding="utf-8")
            with self.assertRaises(SystemExit):
                env.main(["combine", "--output-dir", directory])
            self.assertEqual(marker.read_text(encoding="utf-8"), "original")

    def test_tools_complete_does_not_claim_full_environment_complete(self):
        value = env.report("tools", [env.item(key, "PASS", {"test": True}) for key in env.TOOLS_IDS])
        self.assertNotIn("标准工作流环境完整", env.brief(value))
        self.assertIn("完整环境需合并两侧证据", env.brief(value))

    def test_review_missing_item_requires_action(self):
        value, review = self.make_review()
        review["checks"][0]["state"] = "MISSING"
        with runtime_directory() as directory:
            (Path(directory) / "receipt.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                env.apply_review(value, review, directory)


if __name__ == "__main__":
    unittest.main()
