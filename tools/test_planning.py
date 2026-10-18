import copy
import json
from pathlib import Path
import unittest

import planning
from test_environment_check import runtime_directory


def fixture(root):
    (root / "intake.json").write_text(json.dumps({"schema": "dft-research-intake-check/v1", "task_id": "synthetic-plan",
        "status": "READY_FOR_PLAN_DISCUSSION", "blockers": []}), encoding="utf-8")
    (root / "model.txt").write_text("Synthetic source identity, not a real structure", encoding="utf-8")
    (root / "method.txt").write_text("Synthetic method evidence, not a scientific recommendation", encoding="utf-8")
    stage = {"id": "static1", "template": "static", "objective": "synthetic comparison", "external_field": False,
             "fixed_cell": True, "soc": False, "model_source_ref": "model.txt", "settings_basis": "synthetic method note",
             "completion_criteria": "synthetic declared criteria", "choices": {key: "explicit synthetic choice" for key in planning.catalog()["static"]["required_choices"]},
             "acceptance_checks": {key: "explicit synthetic criterion" for key in planning.catalog()["static"]["required_checks"]},
             "deliverables": planning.catalog()["static"]["expected_outputs"]}
    return {"schema": "dft-calculation-plan/v1", "task_id": "synthetic-plan", "intake_ref": "intake.json",
            "research_question": "synthetic stability question", "why_needed": "evidence gap", "method_rationale": "synthetic rationale",
            "method_evidence_refs": ["method.txt"], "precision_and_comparability": "explicit same reference conditions",
            "interpretation_limits": "synthetic only", "resources": {"description": "planned resources", "basis": "available inventory"},
            "cost": {"state": "UNKNOWN", "basis": "No comparable benchmark; no prediction invented"},
            "stop_conditions": ["scope change or unresolved anomaly"], "execution_scope": "local preparation only", "handoff_point": "prepared package",
            "stages": [stage]}


class PlanningTests(unittest.TestCase):
    def test_complete_plan_is_reviewable_never_authorized(self):
        with runtime_directory() as directory:
            value = fixture(Path(directory))
            result = planning.validate(value, directory)
            self.assertEqual(result["status"], "READY_FOR_USER_REVIEW")
            self.assertFalse(result["execution_authorized"])

    def test_unconfirmed_intake_blocks_plan(self):
        with runtime_directory() as directory:
            root = Path(directory)
            value = fixture(root)
            source = json.loads((root / "intake.json").read_text())
            source["status"] = "NEEDS_CLARIFICATION"
            (root / "intake.json").write_text(json.dumps(source))
            self.assertIn("INTAKE_NOT_CONFIRMED_OR_WRONG_TASK", planning.validate(value, directory)["blockers"])

    def test_no_default_scientific_choice_and_unsupported_scope(self):
        with runtime_directory() as directory:
            value = fixture(Path(directory))
            value["stages"][0]["choices"].pop("spin")
            self.assertIn("static1:CHOICE_REQUIRED:spin", planning.validate(value, directory)["blockers"])
            value["stages"][0]["external_field"] = True
            self.assertIn("static1:OUTSIDE_VALIDATED_SCOPE", planning.validate(value, directory)["blockers"])

    def test_estimated_cost_needs_range_uncertainty_and_evidence(self):
        with runtime_directory() as directory:
            value = fixture(Path(directory))
            value["cost"] = {"state": "ESTIMATED", "basis": "benchmark claim", "wall_hours_range": [8, 2]}
            blockers = planning.validate(value, directory)["blockers"]
            self.assertIn("COST_RANGE_OR_UNCERTAINTY_INVALID", blockers)
            self.assertIn("COST_ESTIMATE_EVIDENCE_MISSING", blockers)
            value["cost"].update(wall_hours_range=[2, 8], uncertainty="different size", evidence_refs=["method.txt"])
            self.assertNotIn("COST_RANGE_OR_UNCERTAINTY_INVALID", planning.validate(value, directory)["blockers"])

    def test_multi_stage_reuses_exact_existing_dependency_plan(self):
        with runtime_directory() as directory:
            root = Path(directory)
            value = fixture(root)
            second = copy.deepcopy(value["stages"][0])
            second["id"] = "static2"
            value["stages"].append(second)
            self.assertIn("EXISTING_DEPENDENCY_PLAN_REQUIRED", planning.validate(value, directory)["blockers"])
            dependency = {"schema": "vasp-property-plan/v1", "nodes": [{"stage_id": "static1"}, {"stage_id": "wrong"}], "execution_authorized": False}
            (root / "dependencies.json").write_text(json.dumps(dependency))
            value["dependency_plan_ref"] = "dependencies.json"
            self.assertIn("DEPENDENCY_PLAN_STAGE_IDENTITY_MISMATCH", planning.validate(value, directory)["blockers"])
            dependency["nodes"][1]["stage_id"] = "static2"
            (root / "dependencies.json").write_text(json.dumps(dependency))
            self.assertEqual(planning.validate(value, directory)["status"], "READY_FOR_USER_REVIEW")

    def test_output_requirements_not_optional(self):
        with runtime_directory() as directory:
            value = fixture(Path(directory))
            value["stages"][0]["deliverables"] = ["OUTCAR"]
            self.assertIn("static1:OUTPUT_CONTRACT_INCOMPLETE", planning.validate(value, directory)["blockers"])

    def test_cli_delivers_plain_summary_and_technical_appendix(self):
        with runtime_directory() as directory:
            root = Path(directory)
            value = fixture(root)
            (root / "plan.json").write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(planning.main(["--plan", str(root / "plan.json"), "--workspace", directory, "--output-dir", str(root / "report")]), 0)
            report = (root / "report/README.md").read_text(encoding="utf-8")
            self.assertIn("停止条件", report)
            self.assertIn("计算清单与技术附录", report)
            self.assertIn("UNKNOWN", report)


    def test_blocked_cli_writes_issue_sidecar(self):
        with runtime_directory() as temp:
            root=Path(temp);plan=fixture(root);plan['research_question']=''
            source=root/'plan.json';source.write_text(json.dumps(plan),encoding='utf-8')
            output=root/'blocked'
            self.assertEqual(planning.main(['--plan',str(source),'--workspace',str(root),'--output-dir',str(output)]),1)
            issues=json.loads((output/'issues.json').read_text(encoding='utf-8'))
            self.assertEqual(issues['status'],'BLOCKED')
            self.assertTrue(issues['issues'][0]['user_decision'])
            self.assertIn('plan_check.json',(output/'ISSUES.md').read_text(encoding='utf-8'))

if __name__ == "__main__":
    unittest.main()
