"""Check scientific-plan completeness against fixed types; never choose parameters.

References and declared choices are checked, not scientific validity. Multi-stage
dependency planning reuses the existing property_plan receipt instead of building
a second dependency graph here. No preparation, execution or approval occurs.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

from intake import existing, text


def catalog():
    return json.loads(Path(__file__).with_name("plan_templates.json").read_text(encoding="utf-8"))["templates"]


def strings(value):
    return isinstance(value, list) and bool(value) and all(text(part) for part in value)


def validate(plan, root):
    if plan.get("schema") != "dft-calculation-plan/v1" or not text(plan.get("task_id")):
        raise ValueError("Explicit schema and task ID required")
    blockers = []
    for name in ("research_question", "why_needed", "method_rationale", "precision_and_comparability",
                 "interpretation_limits", "execution_scope", "handoff_point"):
        if not text(plan.get(name)):
            blockers.append("MISSING:" + name)
    if not existing(root, plan.get("intake_ref")):
        blockers.append("INTAKE_REFERENCE_MISSING")
    else:
        intake = json.loads((Path(root) / plan["intake_ref"]).read_text(encoding="utf-8-sig"))
        if (intake.get("schema") != "dft-research-intake-check/v1" or intake.get("task_id") != plan["task_id"]
                or intake.get("status") != "READY_FOR_PLAN_DISCUSSION" or intake.get("blockers")):
            blockers.append("INTAKE_NOT_CONFIRMED_OR_WRONG_TASK")
    if not strings(plan.get("stop_conditions")):
        blockers.append("STOP_CONDITIONS_MISSING")
    if not strings(plan.get("method_evidence_refs")) or not all(existing(root, ref) for ref in plan.get("method_evidence_refs", [])):
        blockers.append("METHOD_EVIDENCE_MISSING")
    resources = plan.get("resources", {})
    if not text(resources.get("description")) or not text(resources.get("basis")):
        blockers.append("RESOURCE_SCOPE_OR_BASIS_MISSING")
    cost = plan.get("cost", {})
    if cost.get("state") not in {"ESTIMATED", "UNKNOWN"} or not text(cost.get("basis")):
        blockers.append("COST_BASIS_MISSING")
    elif cost["state"] == "ESTIMATED":
        bounds = cost.get("wall_hours_range")
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds)
                or not 0 <= bounds[0] <= bounds[1] or not text(cost.get("uncertainty"))):
            blockers.append("COST_RANGE_OR_UNCERTAINTY_INVALID")
        if not strings(cost.get("evidence_refs")) or not all(existing(root, ref) for ref in cost.get("evidence_refs", [])):
            blockers.append("COST_ESTIMATE_EVIDENCE_MISSING")
    elif cost.get("wall_hours_range") is not None:
        blockers.append("UNKNOWN_COST_MUST_NOT_CLAIM_A_RANGE")
    templates, stages, seen = catalog(), plan.get("stages", []), set()
    if not isinstance(stages, list) or not stages:
        raise ValueError("An explicit nonempty calculation list is required")
    for stage in stages:
        key = stage.get("id")
        if not text(key) or key in seen:
            raise ValueError("Unique explicit stage IDs required")
        seen.add(key)
        template = templates.get(stage.get("template"))
        if template is None:
            blockers.append(key + ":UNVALIDATED_TEMPLATE")
            continue
        if stage.get("external_field") is not False or stage.get("fixed_cell") is not True or stage.get("soc") is not False:
            blockers.append(key + ":OUTSIDE_VALIDATED_SCOPE")
        for name in ("objective", "settings_basis", "completion_criteria"):
            if not text(stage.get(name)):
                blockers.append(key + ":MISSING:" + name)
        if not existing(root, stage.get("model_source_ref")):
            blockers.append(key + ":MODEL_SOURCE_MISSING")
        choices = stage.get("choices", {})
        for name in template["required_choices"]:
            if not text(choices.get(name)):
                blockers.append(key + ":CHOICE_REQUIRED:" + name)
        checks = stage.get("acceptance_checks", {})
        for name in template["required_checks"]:
            if not text(checks.get(name)):
                blockers.append(key + ":CHECK_REQUIRED:" + name)
        if not strings(stage.get("deliverables")) or not set(template["expected_outputs"]).issubset(stage.get("deliverables", [])):
            blockers.append(key + ":OUTPUT_CONTRACT_INCOMPLETE")
    if len(stages) > 1:
        ref = plan.get("dependency_plan_ref")
        if not existing(root, ref):
            blockers.append("EXISTING_DEPENDENCY_PLAN_REQUIRED")
        else:
            dependency = json.loads((Path(root) / ref).read_text(encoding="utf-8-sig"))
            nodes = dependency.get("nodes", [])
            if (dependency.get("schema") != "vasp-property-plan/v1" or len(nodes) != len(stages)
                    or {node.get("stage_id") for node in nodes} != seen or dependency.get("execution_authorized") is not False):
                blockers.append("DEPENDENCY_PLAN_STAGE_IDENTITY_MISMATCH")
            # Its BLOCKED state can describe not-yet-prepared contracts. Preserve
            # it as a limitation; never turn it into execution authorization.
    return {"schema": "dft-calculation-plan-check/v1", "task_id": plan["task_id"],
            "status": "NEEDS_PLAN_COMPLETION" if blockers else "READY_FOR_USER_REVIEW",
            "blockers": blockers, "stage_count": len(stages),
            "scientific_acceptance": "NOT_AUTHORIZED", "execution_authorized": False,
            "scope": "Completeness only; scientific method, source meaning and user approval require actual review"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Preserve previous outputs; use a new directory")
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    result = validate(plan, args.workspace)
    lines = ["# 计算方案", "", "状态：" + result["status"], ""]
    for name, label in (("research_question", "研究问题"), ("why_needed", "为什么需要计算"),
                        ("method_rationale", "方案依据"), ("precision_and_comparability", "精度与比较条件"),
                        ("interpretation_limits", "适用范围"), ("execution_scope", "拟批准范围"), ("handoff_point", "交回节点")):
        lines.append("- " + label + "：" + str(plan.get(name) or "待明确"))
    lines.extend(["", "资源：" + str(plan.get("resources", {}).get("description") or "待明确"),
                  "成本：" + json.dumps(plan.get("cost", {}), ensure_ascii=False),
                  "", "停止条件：" + json.dumps(plan.get("stop_conditions", []), ensure_ascii=False),
                  "", "方法依据文件：" + json.dumps(plan.get("method_evidence_refs", []), ensure_ascii=False),
                  "", "依赖计划：" + str(plan.get("dependency_plan_ref") or "单项计算；未声明多阶段依赖"),
                  "多阶段依赖的准备缺项以原计划为准，本报告不打开执行授权。", "", "## 计算清单与技术附录", ""])
    for stage in plan["stages"]:
        lines.extend(["### " + stage["id"], "", "目标：" + str(stage.get("objective")), "",
                      "```json", json.dumps(stage, ensure_ascii=False, indent=2), "```", ""])
    lines.extend(["## 待补充", ""] + ["- " + blocker for blocker in result["blockers"]])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "plan_check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "stage_count": result["stage_count"]}))
    return 1 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
