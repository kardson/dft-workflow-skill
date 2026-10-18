"""Read an explicit continuation record; never acts on recommendations."""
import argparse
import hashlib
import json
from pathlib import Path

ACTIONS = {"DRAFT": "CLARIFY_RESEARCH", "PLANNED": "REVIEW_PLAN", "PREPARED": "VERIFY_EXECUTION_AUTHORITY", "SUBMITTED": "QUERY_STATUS_ON_REQUEST", "RUNNING": "QUERY_STATUS_ON_REQUEST", "TERMINAL": "VERIFY_RECOVERY_AUTHORITY", "RECOVERED": "VERIFY_ANALYSIS_BINDINGS", "ANALYZED": "SCIENTIFIC_REVIEW", "ACCEPTED": "REPORT_ACCEPTED_SCOPE"}

def resume(record, workspace):
    root = Path(workspace).resolve(strict=True)
    source = Path(record).resolve(strict=True)
    source.relative_to(root)
    value = json.loads(source.read_text(encoding="utf-8-sig"))
    if value.get("schema") != "dft-continuation/v1" or not value.get("task_id"):
        raise ValueError("Explicit continuation schema and task identity required")
    if value.get("stage") not in ACTIONS or not value.get("writer"):
        raise ValueError("Known stage and responsible writer required")
    refs = value.get("evidence")
    if not isinstance(refs, list) or not refs:
        raise ValueError("Explicit evidence list required")
    problems = []
    for ref in refs:
        path = (root / ref["path"]).resolve()
        path.relative_to(root)
        expected = ref.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("Recorded evidence identity required")
        if not path.is_file():
            problems.append("MISSING:" + ref["path"])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append("CHANGED:" + ref["path"])
    return {"schema": "dft-resume-report/v1", "task_id": value["task_id"],
            "recorded_stage": value["stage"], "writer": value["writer"],
            "recorded_approval_scope": value.get("approval_scope"),
            "status": "BLOCKED" if problems else "CONTEXT_RESTORED",
            "next_action": "RECONCILE_EVIDENCE" if problems else ACTIONS[value["stage"]],
            "problems": problems, "execution_authorized": False,
            "limitations": ["Restored evidence is historical, not current runtime status", "Approval scope is a recorded claim; verify original approval before action", "Recommendations are not executed"]}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = resume(args.record, args.workspace)
    destination = args.output.resolve()
    destination.relative_to(args.workspace.resolve())
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return 1 if result["problems"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
