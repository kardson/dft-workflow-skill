import copy
import json
from pathlib import Path
import unittest

import intake
from test_environment_check import runtime_directory


def request():
    return {"schema": "dft-research-intake/v1", "task_id": "synthetic-intake",
            "question_fields": {key: {"state": "CONFIRMED", "value": "synthetic user answer",
                "confirmation": {"reference": "synthetic-message", "quote": "synthetic user answer"}} for key in intake.FIELDS},
            "materials": [], "evidence_gap": {"summary": "need comparable evidence", "new_calculation": "UNDECIDED", "basis": "existing results not yet comparable"}}


def material(state):
    return {"id": "paper1", "type": "literature", "target": "specific synthetic paper",
            "purpose": "check method evidence", "requirements": "full methods text", "state": state,
            "impact": "method choice remains open", "source": "https://example.invalid/paper"}


class IntakeTests(unittest.TestCase):
    def test_every_unresolved_field_generates_a_question(self):
        value = request()
        for key in intake.FIELDS:
            value["question_fields"][key] = {"state": "UNRESOLVED", "question_to_user": "Please clarify " + key}
        result = intake.validate(value, ".")
        self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(len(result["questions_to_user"]), 4)
        self.assertFalse(result["execution_authorized"])

    def test_asserted_confirmation_without_source_is_blocked(self):
        value = request()
        value["question_fields"]["scope"].pop("confirmation")
        result = intake.validate(value, ".")
        self.assertIn("scope:USER_CONFIRMATION_REQUIRED", result["blockers"])
        self.assertEqual(len(result["questions_to_user"]), 1)

    def test_no_need_for_new_calculation_is_allowed(self):
        value = request()
        value["evidence_gap"].update(new_calculation="NO", basis="existing data answers confirmed scope")
        self.assertEqual(intake.validate(value, ".")["status"], "READY_FOR_PLAN_DISCUSSION")

    def test_found_is_not_obtained(self):
        value = request()
        value["materials"] = [material("FOUND")]
        result = intake.validate(value, ".")
        self.assertEqual(result["material_actions"][0]["action"], "OBTAIN_AND_VERIFY")
        value["materials"][0]["state"] = "OBTAINED"
        self.assertIn("paper1:MATERIAL_FILE_OR_SOURCE_MISSING", intake.validate(value, ".")["blockers"])

    def test_user_fallback_requires_attempts_restriction_and_specific_request(self):
        value = request()
        value["materials"] = [material("USER_REQUIRED")]
        with self.assertRaises(ValueError):
            intake.validate(value, ".")
        value["materials"][0].update(search_attempts=["Synthetic search returned metadata; full text unavailable"],
                                     restriction="Full text requires authorized access", request_to_user="Provide the methods PDF for the identified paper")
        result = intake.validate(value, ".")
        self.assertEqual(result["material_actions"][0]["owner"], "user")

    def test_existing_source_file_and_missing_impact(self):
        with runtime_directory() as directory:
            value = request()
            value["materials"] = [material("EXISTING")]
            path = Path(directory) / "source.txt"
            path.write_text("synthetic evidence", encoding="utf-8")
            value["materials"][0]["file_ref"] = "source.txt"
            self.assertFalse(intake.validate(value, directory)["blockers"])
            path.write_text("", encoding="utf-8")
            self.assertTrue(intake.validate(value, directory)["blockers"])
            value["materials"][0]["file_ref"] = "../outside.txt"
            self.assertTrue(intake.validate(value, directory)["blockers"])
            value["materials"][0] = material("MISSING")
            value["materials"][0].pop("impact")
            with self.assertRaises(ValueError):
                intake.validate(value, directory)

    def test_cli_report_contains_need_purpose_and_user_request(self):
        with runtime_directory() as directory:
            root = Path(directory)
            value = request()
            value["materials"] = [material("USER_REQUIRED")]
            value["materials"][0].update(search_attempts=["Test connector not available"], restriction="capability unavailable", request_to_user="Provide identified paper PDF")
            (root / "request.json").write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(intake.main(["--request", str(root / "request.json"), "--workspace", directory,
                                         "--output-dir", str(root / "report")]), 0)
            report = (root / "report/README.md").read_text(encoding="utf-8")
            for text in ("specific synthetic paper", "full methods text", "Provide identified paper PDF"):
                self.assertIn(text, report)


if __name__ == "__main__":
    unittest.main()
