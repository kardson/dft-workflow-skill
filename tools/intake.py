"""Validate agent-maintained research intake; no search, assumptions or submission.

The agent must ask the user and use actual message/evidence references. This
checker validates the record, not the truth of a quoted conversation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ("research_object", "research_question", "conditions", "scope")
STATES = {"CONFIRMED", "UNRESOLVED"}
MATERIAL_STATES = {"EXISTING", "FOUND", "OBTAINED", "MISSING", "USER_REQUIRED"}
TYPES = {"structure", "literature", "outputs", "notes"}


def text(value):
    return isinstance(value, str) and bool(value.strip())


def existing(root, ref):
    if not text(ref):
        return False
    root = Path(root).resolve()
    path = (root / ref).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path.is_file() and path.stat().st_size > 0


def validate(request, root):
    if request.get("schema") != "dft-research-intake/v1" or not text(request.get("task_id")):
        raise ValueError("Explicit intake schema and task ID required")
    fields = request.get("question_fields", {})
    if set(fields) != set(FIELDS):
        raise ValueError("Provide all four question fields; use UNRESOLVED rather than assumptions")
    blockers, questions = [], []
    for key in FIELDS:
        field = fields[key]
        if field.get("state") not in STATES:
            raise ValueError("Unknown question state")
        if field["state"] == "CONFIRMED":
            confirmation = field.get("confirmation", {})
            if not text(field.get("value")) or not all(text(confirmation.get(name)) for name in ("reference", "quote")):
                blockers.append(key + ":USER_CONFIRMATION_REQUIRED")
                questions.append({"field": key, "question": "请确认或纠正该项候选说明，并保留实际答复依据。"})
        else:
            if not text(field.get("question_to_user")):
                raise ValueError("Unresolved intent needs an explicit question")
            questions.append({"field": key, "question": field["question_to_user"]})
            blockers.append(key + ":UNRESOLVED")
    seen, needs = set(), []
    for material in request.get("materials", []):
        key = material.get("id")
        if not text(key) or key in seen:
            raise ValueError("Material IDs must be explicit and unique")
        seen.add(key)
        if material.get("type") not in TYPES or material.get("state") not in MATERIAL_STATES:
            raise ValueError("Unknown material type or state")
        if not all(text(material.get(name)) for name in ("target", "purpose", "requirements")):
            raise ValueError("Materials need a concrete target, purpose and requirements")
        state = material["state"]
        if state in {"EXISTING", "OBTAINED"}:
            if not existing(root, material.get("file_ref")) or not text(material.get("source")):
                blockers.append(key + ":MATERIAL_FILE_OR_SOURCE_MISSING")
        elif state == "FOUND":
            if not text(material.get("source")):
                raise ValueError("Search hit needs a source; it is not an obtained file")
            needs.append({"id": key, "action": "OBTAIN_AND_VERIFY", "owner": "agent"})
        elif state == "USER_REQUIRED":
            attempts = material.get("search_attempts")
            if (not all(text(material.get(name)) for name in ("restriction", "request_to_user"))
                    or not isinstance(attempts, list) or not attempts or not all(text(attempt) for attempt in attempts)):
                raise ValueError("User fallback needs actual search/capability attempts, restriction and specific request")
            needs.append({"id": key, "action": material["request_to_user"], "owner": "user"})
        else:
            needs.append({"id": key, "action": "SEARCH", "owner": "agent"})
        if state not in {"EXISTING", "OBTAINED"}:
            if not text(material.get("impact")):
                raise ValueError("Unobtained material must describe its impact on the next stage")
    gap = request.get("evidence_gap", {})
    if not text(gap.get("summary")) or gap.get("new_calculation") not in {"YES", "NO", "UNDECIDED"} or not text(gap.get("basis")):
        raise ValueError("Evidence gap and need for new calculation must have an explicit basis; UNDECIDED is allowed")
    return {"schema": "dft-research-intake-check/v1", "task_id": request["task_id"],
            "status": "NEEDS_CLARIFICATION" if blockers else "READY_FOR_PLAN_DISCUSSION",
            "blockers": blockers, "questions_to_user": questions, "material_actions": needs,
            "confirmation_scope": "record references checked; actual user meaning must be verified by the agent",
            "scientific_acceptance": "NOT_AUTHORIZED", "execution_authorized": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Output already exists; preserve earlier records")
    request = json.loads(args.request.read_text(encoding="utf-8-sig"))
    result = validate(request, args.workspace)
    lines = ["# 研究问题与材料核对", "", "任务：" + result["task_id"], "", "状态：" + result["status"], ""]
    for key in FIELDS:
        field = request["question_fields"][key]
        confirmed = field["state"] == "CONFIRMED" and key + ":USER_CONFIRMATION_REQUIRED" not in result["blockers"]
        labels = {"research_object": "研究对象", "research_question": "研究问题", "conditions": "条件", "scope": "研究范围"}
        lines.append("- " + labels[key] + "：" + (field.get("value", "") if confirmed else "待确认；" + field.get("question_to_user", "请确认或纠正候选说明。")))
    lines.extend(["", "证据缺口：" + request["evidence_gap"]["summary"], "", "## 材料补充", ""])
    actions = {action["id"]: action for action in result["material_actions"]}
    for material in request.get("materials", []):
        lines.append("- " + material["target"] + "（" + material["state"] + "）：用途：" + material["purpose"] + "；要求：" + material["requirements"])
        if material.get("source"):
            lines.append("  来源：" + material["source"])
        if material.get("file_ref"):
            lines.append("  文件：" + material["file_ref"])
        if material.get("impact"):
            lines.append("  对后续方案的影响：" + material["impact"])
        if material["id"] in actions:
            action = actions[material["id"]]
            lines.append("  下一动作：" + action["owner"] + "；" + action["action"])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "intake_check.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "questions": len(result["questions_to_user"]), "material_actions": len(result["material_actions"])}, ensure_ascii=True))
    return 1 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
