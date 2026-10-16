"""Traceable result parsing, evidence review and canonical task-card update.

Existing numerical parsers perform calculations. This module keeps evidence,
interpretation and scientific acceptance separate. It never runs VASP or SSH.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from intake import existing, text
from planning import validate as validate_plan
from preparation import digest, template_matches
from recovery import ALLOWED

PARSE = '''import importlib,importlib.metadata as metadata,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from vasp_executor import postcheck
source=Path(sys.argv[2])
checked=postcheck(sys.argv[4],source,source,sys.argv[5])
module='result_analysis' if (Path(sys.argv[1])/'result_analysis.py').is_file() else 'vasp_result_analysis'
summary=importlib.import_module(module).analyze(source,Path(sys.argv[3])/'parser')
versions={name:metadata.version(name) for name in ('numpy','ase','pymatgen','matplotlib')}
(Path(sys.argv[3])/'backend.json').write_text(json.dumps({'summary':summary,'postcheck':checked,'versions':versions,'python':sys.version,'result_module':module},indent=2),encoding='utf-8')
print('DFT_ANALYSIS_JSON:'+json.dumps({'status':summary['status'],'input_check':checked['input']['passed'],'postcheck':checked['passed']}))
'''


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def private_path(workspace, ref):
    root = Path(workspace).resolve()
    path = (root / ref).resolve()
    path.relative_to(root)
    if not path.is_file():
        raise ValueError("Evidence file unavailable: " + str(ref))
    return path


def sources(recovered, workspace):
    if recovered.get("schema") != "dft-recovery-handoff/v1" or recovered.get("recovery_performed") is not True:
        raise ValueError("Actual recovery handoff required")
    source = Path(recovered["analysis_source"]).resolve()
    source.relative_to(Path(workspace).resolve())
    if not source.is_dir():
        raise ValueError("Recovered analysis directory unavailable")
    if any((source / name).exists() for name in ("POTCAR", "WAVECAR", "CHGCAR")):
        raise ValueError("Sensitive/restart files are not part of the analysis view")
    for name, record in recovered.get("files", {}).items():
        if name not in ALLOWED or name == "status.json":
            raise ValueError("Unexpected recovered source role")
        path = private_path(workspace, record["path"])
        if path.parent != source or path.name != name or (source / name).is_symlink():
            raise ValueError("Recovered file location changed")
        if path.stat().st_size != record.get("bytes") or digest(path) != record.get("sha256"):
            raise ValueError("Recovered source content changed: " + name)
    # Refuse unrecorded core files: a parser must not silently consume local
    # additions absent from the recovered-source provenance.
    for name in ALLOWED:
        if (source / name).exists() and name not in recovered["files"]:
            raise ValueError("Unrecorded analysis source: " + name)
    return source


def parse(recovery_path, plan_path, stage_id, approved, requirements, workspace, tool_root, python, output, runner=subprocess.run, preparation=None):
    if preparation is None:
        raise ValueError("Prepared-package handoff is required to bind approved output requirements")
    root = Path(workspace).resolve()
    for path in (recovery_path, plan_path, approved, requirements):
        private_path(root, str(Path(path).resolve()))
    recovered, plan = load(recovery_path), load(plan_path)
    if validate_plan(plan, root)["blockers"]:
        raise ValueError("Plan is incomplete or research intent unconfirmed")
    if recovered.get("identity", {}).get("task_id") != plan.get("task_id"):
        raise ValueError("Recovery belongs to another research task")
    stages = [stage for stage in plan["stages"] if stage["id"] == stage_id]
    templates = load(Path(__file__).with_name("analysis_templates.json"))["templates"]
    if len(stages) != 1 or stages[0].get("template") not in templates:
        raise ValueError("Explicit supported plan stage required")
    source = sources(recovered, root)
    spec = load(approved)
    if not template_matches(stages[0]["template"], spec):
        raise ValueError("Analysis template does not match the approved calculation type")
    if any(spec.get(key) != recovered["identity"].get(key) for key in ("task_id", "unit_id")):
        raise ValueError("Approved numerical specification belongs to another task/unit")
    if preparation:
        prepared = load(private_path(root, str(Path(preparation).resolve())))
        if (prepared.get("status") != "LOCAL_PACKAGE_CHECKED" or prepared.get("task_id") != plan["task_id"]
                or prepared.get("stage_id") != stage_id or prepared.get("plan_sha256") != digest(plan_path)
                or prepared.get("candidate_manifest_sha256") != digest(approved)):
            raise ValueError("Prepared package is not bound to this plan and numerical manifest")
        if prepared.get("candidate_output_requirements_sha256") != digest(requirements):
            raise ValueError("Output requirements are not bound to the prepared approved package; rerun preparation checks for old records")
    output = Path(output).resolve()
    output.relative_to(root)
    if output.exists() or output == source or source in output.parents or output in source.parents:
        raise ValueError("New analysis output outside original sources required")
    output.mkdir(parents=True, exist_ok=False)
    original_stats = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in (source / name for name in recovered.get("files", {}))}
    driver = output / "analysis_driver.py"
    driver.write_text(PARSE, encoding="utf-8")
    argv = [str(python), "-B", str(driver), str(Path(tool_root).resolve()), str(source), str(output), str(Path(approved).resolve()), str(Path(requirements).resolve())]
    errors = []
    backend_status = "SUCCEEDED"
    try:
        child = runner(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        markers = [line for line in child.stdout.splitlines() if line.startswith("DFT_ANALYSIS_JSON:")]
        if child.returncode != 0 or len(markers) != 1 or not (output / "backend.json").is_file():
            raise ValueError("Numerical backend did not return one completed analysis record")
        backend = load(output / "backend.json")
        if (not isinstance(backend.get("summary"), dict) or backend["summary"].get("status") not in {"COMPLETE_XML", "PARTIAL"}
                or not isinstance(backend.get("postcheck"), dict)):
            raise ValueError("Numerical backend record is incomplete or unsupported")
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        backend_status = "FAILED"
        errors.append(type(error).__name__ + ": " + str(error))
        backend = {"summary": {"status": "UNAVAILABLE"}, "postcheck": {"passed": False}}
    if any((path.stat().st_size, path.stat().st_mtime_ns) != before for path, before in original_stats.items()):
        raise ValueError("Recovered sources changed during parsing; preserve output but do not issue conclusions")
    summary, checked = backend["summary"], backend["postcheck"]
    observations = {"parser_status": summary.get("status", "PARTIAL"), "terminal_state": recovered.get("terminal_state"),
                    "electronic_converged": summary.get("electronic_converged"),
                    "ionic_converged": summary.get("ionic_converged") if templates[stages[0]["template"]]["ionic_convergence_required"] else "NOT_APPLICABLE_TO_STATIC_TASK",
                    "actual_input_check": checked.get("input", {}).get("passed"), "postcheck": checked.get("passed"),
                    "parameter_comparison": checked.get("parameter_comparison", {}).get("status", "UNKNOWN"),
                    "steps": summary.get("steps", [])}
    warnings = list(errors)
    invalid_numbers = []
    def finite_values(value, location="observations"):
        if isinstance(value, float) and not math.isfinite(value):
            invalid_numbers.append(location)
            return None
        if isinstance(value, dict):
            return {key: finite_values(part, location + "/" + str(key)) for key, part in value.items()}
        if isinstance(value, list):
            return [finite_values(part, location + "/" + str(index)) for index, part in enumerate(value)]
        return value
    observations = finite_values(observations)
    if invalid_numbers:
        warnings.append("非有限数值已保留为未知：" + ", ".join(invalid_numbers))
    if recovered.get("missing"):
        warnings.append("原始输出缺失：" + ", ".join(recovered["missing"]))
    if not recovered.get("declared_output_contract_available") or recovered.get("declared_outputs_missing"):
        warnings.append("回收核心清单不代表全部批准输出合同已满足，需要核实输出合同及缺项。")
    if summary.get("status") != "COMPLETE_XML":
        warnings.append("XML 结果不完整或解析失败，不能由备用解析补作完整收敛结论。")
    if checked.get("passed") is not True:
        warnings.append("实际输入/设置、运行结束或输出契约检查存在缺口，查看 postcheck 依据。")
    for name in ("parse_error", "oszicar_error", "outcar_error"):
        if summary.get(name):
            warnings.append(name + ": " + str(summary[name]))
    artifacts = []
    for name in ("summary.json", "ionic_steps.csv", "energy.png", "free_force.png", "final_geometry.extxyz"):
        path = output / "parser" / name
        if path.is_file():
            artifacts.append({"path": str(path), "sha256": digest(path), "source": str(recovery_path), "method": "existing result analyze()", "argv": argv[:2] + ["existing result analysis", str(source)]})
    result = {"schema": "dft-analysis-facts/v1", "task_id": plan["task_id"], "stage_id": stage_id,
              "source_observed_utc": recovered.get("export_observed_utc"), "analyzed_at_utc": datetime.now(timezone.utc).isoformat(),
              "identity": recovered["identity"], "template": stages[0]["template"], "research_question": plan["research_question"],
              "plan_ref": str(Path(plan_path).resolve()), "plan_sha256": digest(plan_path),
              "recovery_ref": str(Path(recovery_path).resolve()), "recovery_sha256": digest(recovery_path),
              "approved_spec_ref": str(Path(approved).resolve()), "approved_spec_sha256": digest(approved),
              "requirements_ref": str(Path(requirements).resolve()), "requirements_sha256": digest(requirements),
              "preparation_ref": str(Path(preparation).resolve()) if preparation else None,
              "preparation_sha256": digest(preparation) if preparation else None,
              "analysis_driver": str(driver), "analysis_driver_sha256": digest(driver),
              "analysis_command": argv, "backend_versions": backend.get("versions", {}),
              "backend_status": backend_status,
              "backend_ref": str(output / "backend.json") if backend_status == "SUCCEEDED" else None,
              "backend_sha256": digest(output / "backend.json") if backend_status == "SUCCEEDED" else None,
              "observations": observations, "warnings": warnings, "artifacts": artifacts,
              "required_scientific_checks": templates[stages[0]["template"]]["checks"],
              "interpretation": "PENDING_AGENT_REVIEW" if backend_status == "SUCCEEDED" else "BLOCKED_BACKEND_FAILURE", "scientific_acceptance": "NOT_GRANTED_BY_TOOL"}
    (output / "facts.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    next_step = "仍需 agent 对照原问题解释结果、核对可比性与科学用途。" if backend_status == "SUCCEEDED" else "分析程序未成功完成；先解决后端故障再重新分析，不能交付为计算结果。"
    (output / "README.md").write_text("# 分析事实与待审阅项\n\n研究问题：" + plan["research_question"] + "\n\n后端状态：" + backend_status + "\n\n解析状态：" + str(observations["parser_status"]) + "\n\n" + next_step + "\n\n" + "\n".join("- " + warning for warning in warnings) + "\n", encoding="utf-8")
    return result


def update_card(path, task_id, content):
    path = Path(path)
    begin, end = "<!-- AGENT_TASK_CARD_BEGIN:" + task_id + " -->", "<!-- AGENT_TASK_CARD_END:" + task_id + " -->"
    block = begin + "\n" + content + "\n" + end
    if path.is_symlink():
        raise ValueError("Canonical task card cannot be a symlink")
    before = path.read_bytes() if path.exists() else None
    if before is not None:
        old = before.decode("utf-8-sig")
        if old.count(begin) != 1 or old.count(end) != 1 or old.index(begin) > old.index(end):
            raise ValueError("Existing card needs one explicit matching task slot; preserve its history")
        text_value = old[:old.index(begin)] + block + old[old.index(end) + len(end):]
    else:
        text_value = block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".agent-card-tmp")
    with temporary.open("xb") as handle:
        handle.write((b"\xef\xbb\xbf" if before and before.startswith(b"\xef\xbb\xbf") else b"") + text_value.encode("utf-8"))
    if path.exists() and path.read_bytes() != before:
        raise ValueError("Canonical card changed concurrently; preserve staged update")
    temporary.replace(path)


def render(facts_path, review_path, workspace, output, task_card=None):
    root = Path(workspace).resolve()
    facts_path, review_path = private_path(root, str(Path(facts_path).resolve())), private_path(root, str(Path(review_path).resolve()))
    facts, review = load(facts_path), load(review_path)
    if facts.get("backend_status") != "SUCCEEDED":
        raise ValueError("Successful analysis backend evidence required before result delivery")
    if digest(private_path(root, facts["backend_ref"])) != facts["backend_sha256"]:
        raise ValueError("Analysis backend evidence changed after fact collection")
    if (facts.get("schema") != "dft-analysis-facts/v1" or review.get("schema") != "dft-result-review/v1"
            or review.get("task_id") != facts["task_id"] or review.get("facts_sha256") != digest(facts_path)
            or not text(review.get("reviewed_by"))):
        raise ValueError("Review must bind actual facts and reviewer")
    for name in ("answer", "confidence_and_basis", "comparability", "limits", "next_action", "next_action_reason", "next_action_owner"):
        if not text(review.get(name)):
            raise ValueError("Review must explicitly explain " + name)
    for group in ("observations", "interpretations"):
        if not isinstance(review.get(group), list) or not review[group]:
            raise ValueError("Separate observations and interpretations are required")
        for claim in review[group]:
            if not text(claim.get("text")) or not claim.get("evidence_refs"):
                raise ValueError("Every observation/interpretation needs evidence")
            for ref in claim["evidence_refs"]:
                path = private_path(root, ref.split("#", 1)[0])
                if "#" in ref and path.suffix == ".json":
                    pointer = ref.split("#", 1)[1]
                    if not pointer.startswith("/"):
                        raise ValueError("JSON evidence needs an explicit JSON pointer")
                    value = load(path)
                    for token in pointer[1:].split("/"):
                        token = token.replace("~1", "/").replace("~0", "~")
                        try:
                            value = value[int(token)] if isinstance(value, list) else value[token]
                        except (KeyError, IndexError, TypeError, ValueError) as error:
                            raise ValueError("Evidence field does not exist") from error
    for artifact in facts.get("artifacts", []):
        path = private_path(root, artifact["path"])
        if digest(path) != artifact["sha256"]:
            raise ValueError("Analysis artifact changed after fact collection")
    for key in ("plan", "recovery", "approved_spec", "requirements"):
        if digest(private_path(root, facts[key + "_ref"])) != facts[key + "_sha256"]:
            raise ValueError("Analysis source record changed after fact collection")
    sources(load(private_path(root, facts["recovery_ref"])), root)
    if digest(private_path(root, facts['analysis_driver'])) != facts['analysis_driver_sha256']:
        raise ValueError('Analysis method changed after fact collection')
    if facts.get('preparation_ref') and digest(private_path(root, facts['preparation_ref'])) != facts['preparation_sha256']:
        raise ValueError('Prepared-package record changed after fact collection')
    attachments = []
    for artifact in review.get("supporting_artifacts", []):
        if artifact.get("kind") not in {"geometry", "reference", "total-dos", "macro", "hdf5", "diagnose"}:
            raise ValueError("Additional analysis is outside the validated generic scope")
        if not artifact.get("source_refs"):
            raise ValueError("Additional plots need explicit sources")
        refs = [artifact.get("path"), artifact.get("data_ref"), artifact.get("method_ref"), *artifact["source_refs"]]
        if not all(text(ref) for ref in refs):
            raise ValueError("Additional plots need data, method and source references")
        for ref in refs:
            private_path(root, ref)
        attachments.append(artifact)
    output = Path(output).resolve()
    output.relative_to(root)
    if output.exists():
        raise ValueError("Result output already exists")
    lines = ["# 结果审阅", "", "研究问题：" + facts["research_question"], "", "本次判断：" + review["answer"], "",
             "可信程度与依据：" + review["confidence_and_basis"], "", "比较条件：" + review["comparability"], "", "## 实际观察", ""]
    for group, title in (("observations", "观察"), ("interpretations", "解释")):
        if group == "interpretations":
            lines.extend(["", "## 解释与证据", ""])
        for claim in review[group]:
            links = ", ".join("[依据](<" + str(private_path(root, ref.split("#", 1)[0])) + ("#" + ref.split("#", 1)[1] if "#" in ref else "") + ">)" for ref in claim["evidence_refs"])
            lines.append("- " + claim["text"] + " " + links)
    lines.extend(["", "## 限制与下一步", "", review["limits"], "", "下一步：" + review["next_action"],
                  "理由：" + review["next_action_reason"], "负责人：" + review["next_action_owner"],
                  "", "科学接受仍以负责人的明确决定为准，本工具不授予接受或新计算授权。", "", "## 检查与图表", ""])
    if review.get("scientific_decision_ref"):
        decision = private_path(root, review["scientific_decision_ref"])
        lines.extend(["[科学审阅决定](<" + str(decision) + ">)", ""])
    lines.extend("- " + warning for warning in facts["warnings"])
    for artifact in facts.get("artifacts", []):
        lines.append("- [" + Path(artifact["path"]).name + "](" + artifact["path"] + ")；来源与处理方法见 facts.json。")
    for artifact in attachments:
        lines.append("- [" + artifact["kind"] + "](<" + str(private_path(root, artifact["path"])) + ">)；[数据](<" + str(private_path(root, artifact["data_ref"])) + ">)；[方法](<" + str(private_path(root, artifact["method_ref"])) + ">)。")
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "README.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    record = {"schema": "dft-result-delivery/v1", "task_id": facts["task_id"], "identity": facts["identity"],
              "facts_ref": str(facts_path), "review_ref": str(review_path), "report": str(report_path),
              "scientific_acceptance": "NOT_GRANTED_BY_TOOL", "new_calculation_authorized": False}
    record["supporting_artifacts"] = attachments
    cell = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
    card = ("## 简明任务卡\n\n| 项目 | 当前记录 |\n|---|---|\n| 研究问题 | " + cell(facts["research_question"]) +
            " |\n| 当前阶段 | 结果已整理；解析完整性：" + cell(facts["observations"]["parser_status"]) +
            "；科学接受以负责人决定为准 |\n| 证据与时间 | " + cell(facts.get("source_observed_utc") or "源时间未知") + "；[结果报告](<" + str(report_path) +
            ">) |\n| 结论与限制 | " + cell(review["answer"]) + "；" + cell(review["limits"]) +
            " |\n| 下一步与负责人 | " + cell(review["next_action"]) + "；" + cell(review["next_action_owner"]) + " |")
    if task_card:
        if Path(task_card).is_symlink():
            raise ValueError("Use the canonical task-card path, not a symlink")
        target = Path(task_card).resolve()
        target.relative_to(root)
        update_card(target, facts["task_id"], card)
        record["canonical_task_card"] = str(target)
    else:
        record["task_card_update"] = "NOT_REQUESTED; route writer must update its canonical slot"
    (output / "delivery.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    analyze = sub.add_parser("analyze")
    for name in ("recovery", "plan", "approved-manifest", "requirements", "tool-root"):
        analyze.add_argument("--" + name, required=True, type=Path)
    analyze.add_argument("--stage-id", required=True)
    analyze.add_argument("--preparation", required=True, type=Path)
    analyze.add_argument("--python", type=Path, default=Path(sys.executable))
    deliver = sub.add_parser("render")
    deliver.add_argument("--facts", required=True, type=Path)
    deliver.add_argument("--review", required=True, type=Path)
    deliver.add_argument("--task-card", type=Path)
    for command in (analyze, deliver):
        command.add_argument("--workspace", required=True, type=Path)
        command.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.mode == "analyze":
        result = parse(args.recovery, args.plan, args.stage_id, args.approved_manifest, args.requirements,
                       args.workspace, args.tool_root, args.python, args.output_dir, preparation=args.preparation)
        print(json.dumps({"parser_status": result["observations"]["parser_status"], "backend_status": result["backend_status"], "interpretation": result["interpretation"]}))
        if result["backend_status"] != "SUCCEEDED":
            return 1
    else:
        result = render(args.facts, args.review, args.workspace, args.output_dir, args.task_card)
        print(json.dumps({"report": result["report"], "scientific_acceptance": result["scientific_acceptance"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
