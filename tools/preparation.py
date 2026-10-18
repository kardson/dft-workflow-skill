"""Audit an existing complete candidate against an explicit approved plan.

Generation stays with the existing approved-bundle/VASPKIT workflow. This layer
reuses its actual-input checker; it never edits inputs, opens a gate or launches.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from intake import existing, text
from planning import validate as validate_plan

FORBIDDEN = {"POTCAR", "WAVECAR", "CHGCAR", "OUTCAR", "OSZICAR", "vasprun.xml", "STOPCAR"}
SCIENTIFIC = ("incar", "structure", "kpoints", "restart", "parallel", "paw_identity")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and subset(value, actual[key]) for key, value in expected.items())
    return type(expected) is type(actual) and expected == actual


def template_matches(template, manifest):
    incar, restart = manifest.get("incar", {}), manifest.get("restart", {})
    nsw = incar.get("NSW")
    if type(nsw) is not int:
        return False
    mode = restart.get("mode")
    if mode is None and restart.get("fresh") is True:
        mode = "fresh"
    if template in {"static", "atomref"}:
        return nsw == 0 and mode == "fresh" and (template != "atomref" or manifest.get("structure", {}).get("nions") == 1)
    return (template in {"relax_fresh", "relax_warm"} and nsw > 0 and incar.get("ISIF") == 2
            and incar.get("IBRION") in (1, 2) and mode == ("fresh" if template == "relax_fresh" else "wavefunction_and_charge_scf"))


def member(root, name):
    if not text(name) or Path(name).name != name or name in {".", ".."} or name in FORBIDDEN:
        raise ValueError("Unsafe or forbidden package member")
    path = root / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Required regular nonempty package member unavailable: " + name)
    path.resolve().relative_to(root.resolve())
    return path


CHECK = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vasp_executor import validate_inputs
root = Path(sys.argv[2])
result = {'actual_inputs': validate_inputs(root/'input_manifest.json', root)}
if sys.argv[3] == 'static':
    from static_delivery_check import check_delivery_manifest
    result['delivery'] = check_delivery_manifest(root)
else:
    from practical_execution_builder import describe, RUNTIME_FILES
    contract = json.loads(Path(sys.argv[4]).read_text(encoding='utf-8-sig'))
    inputs = json.loads((root/'input_manifest.json').read_text(encoding='utf-8-sig'))
    expected = describe(inputs, contract['execution_source'], contract['requirements_source'], contract['environment'])
    def exact(expected, actual):
        if type(expected) is not type(actual):
            return False
        if isinstance(expected, dict):
            return expected.keys() == actual.keys() and all(exact(value, actual[key]) for key, value in expected.items())
        if isinstance(expected, list):
            return len(expected) == len(actual) and all(exact(left, right) for left, right in zip(expected, actual))
        return expected == actual
    requirements_match = exact(expected['output_requirements'], json.loads((root/'output_requirements.json').read_text(encoding='utf-8-sig')))
    actual = json.loads((root/'execution_manifest.json').read_text(encoding='utf-8-sig'))
    execution_id = actual.get('execution_id')
    expected.pop('execution_id', None)
    actual.pop('execution_id', None)
    checksums = json.loads((root/'runtime_checksums.json').read_text(encoding='utf-8-sig'))
    standard_modules = all(name in checksums and not (root/name).is_symlink() and (root/name).resolve().is_relative_to(root.resolve())
                           and (root/name).read_bytes() == (Path(sys.argv[1])/name).read_bytes() for name in RUNTIME_FILES)
    base = '#!/usr/bin/env bash\\nset -Eeuo pipefail\\nROOT="$(cd -P -- "$(dirname -- "$0")" && pwd)"\\nexec ' + contract['environment']['python_bin'] + ' "$ROOT/practical_runtime.py" run --package "$ROOT"\\n'
    scripts = {'run_task.sh': base, 'prepare_runtime.sh': base.replace('practical_runtime.py" run', 'practical_runtime.py" prepare'),
               'launch_supervisor.sh': base.replace('practical_runtime.py" run --package "$ROOT"', 'job_watch.py" "$ROOT/watch.json"')}
    standard_scripts = all((root/name).read_bytes() == data.encode() for name, data in scripts.items())
    diagnostic = expected['task_profile'] in ('fixed_geometry_warm_performance_diagnostic', 'fixed_geometry_warm_rmm_performance_diagnostic')
    watch = dict(job=expected['task_id'], task_id=expected['task_id'], unit_id=expected['unit_id'], execution_id=execution_id,
                 command=[contract['environment']['python_bin'], './practical_runtime.py', 'run', '--package', expected['remote_batch_dir']],
                 cases=[dict(path=expected['case'], kind='static' if diagnostic else 'relaxation', postcheck_receipt='postcheck.json',
                             task_id=expected['task_id'], unit_id=expected['unit_id'], execution_id=execution_id)], notify_argv=[])
    watch_matches = json.loads((root/'watch.json').read_text(encoding='utf-8-sig')) == watch
    result['delivery'] = {'passed': expected == actual and requirements_match and standard_modules and standard_scripts and watch_matches,
                          'existing_describe_reused': True, 'runtime_modules_match': standard_modules, 'scripts_match': standard_scripts,
                          'output_requirements_match': requirements_match}
print('DFT_PREP_JSON:' + json.dumps(result))
'''


def backend(root, tool_root, python, kind, runtime_contract=None):
    child = subprocess.run([str(python), "-B", "-c", CHECK, str(Path(tool_root).resolve()), str(root.resolve()), kind, str(runtime_contract or "")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    values = [line[len("DFT_PREP_JSON:"):] for line in child.stdout.splitlines() if line.startswith("DFT_PREP_JSON:")]
    if child.returncode != 0 or len(values) != 1:
        raise ValueError("Existing checker failed; no program failure or scientific result inferred")
    return json.loads(values[0])


def audit(candidate, plan_path, approval_path, approved_manifest, workspace, tool_root, python=sys.executable, checker=backend):
    root, workspace = Path(candidate).resolve(), Path(workspace).resolve()
    if Path(candidate).is_symlink() or not root.is_dir():
        raise ValueError("Candidate must be an existing real directory")
    root.relative_to(workspace)
    for path in (plan_path, approval_path, approved_manifest):
        if not existing(workspace, str(Path(path).resolve())):
            raise ValueError("Plan, approval and approved manifest must exist within the workspace")
    plan, approval, approved = read(plan_path), read(approval_path), read(approved_manifest)
    blockers = []
    if validate_plan(plan, workspace)["blockers"]:
        blockers.append("PLAN_NOT_COMPLETE")
    confirmation = approval.get("user_confirmation", {})
    if (approval.get("schema") != "dft-plan-approval/v1" or approval.get("task_id") != plan.get("task_id")
            or approval.get("plan_sha256") != digest(plan_path) or approval.get("approved_manifest_sha256") != digest(approved_manifest)
            or not text(confirmation.get("reference")) or not text(confirmation.get("quote"))
            or not existing(workspace, approval.get("scientific_decision_ref"))):
        blockers.append("APPROVAL_OR_EXACT_SOURCE_BINDING_MISSING")
    stages = [stage for stage in plan.get("stages", []) if stage.get("id") == approval.get("stage_id")]
    if plan.get("schema") != "dft-calculation-plan/v1" or len(stages) != 1:
        blockers.append("PLAN_STAGE_IDENTITY_MISMATCH")
    actual = read(member(root, "input_manifest.json"))
    for name in ("POSCAR", "INCAR", "KPOINTS"):
        member(root, name)
    if approved.get("task_id") != plan.get("task_id") or actual.get("task_id") != plan.get("task_id"):
        blockers.append("PACKAGE_TASK_IDENTITY_MISMATCH")
    strict = {"incar", "structure", "kpoints", "restart", "parallel"}
    differences = [key for key in SCIENTIFIC if key not in approved or not
                   (subset(approved[key], actual.get(key)) and subset(actual.get(key), approved[key]) if key in strict
                    else subset(approved[key], actual.get(key)))]
    for key in ("unit_id", "source", "delivery_identity"):
        if key in approved and not subset(approved[key], actual.get(key)):
            differences.append(key)
    environment_key = "environment" if "environment" in approved else "environment_id"
    if environment_key not in approved or not subset(approved[environment_key], actual.get(environment_key)):
        differences.append(environment_key)
    if differences:
        blockers.append("SCIENTIFIC_SPECIFICATION_CHANGED")
    if any((root / name).exists() for name in FORBIDDEN):
        blockers.append("SENSITIVE_OR_RESULT_FILES_IN_CANDIDATE")
    execution = read(member(root, "execution_manifest.json"))
    if execution.get("task_id") != plan.get("task_id"):
        blockers.append("EXECUTION_TASK_IDENTITY_MISMATCH")
    kind = "static" if actual.get("delivery_identity") else "fixed_cell"
    if len(stages) == 1:
        requested = stages[0].get("template")
        if not template_matches(requested, actual):
            blockers.append("PLAN_TEMPLATE_PACKAGE_MISMATCH")
    runtime_contract = None
    if kind == "fixed_cell":
        contract_ref = approval.get("runtime_contract_ref")
        if not existing(workspace, contract_ref):
            blockers.append("APPROVED_RUNTIME_CONTRACT_MISSING")
        else:
            runtime_contract = (workspace / contract_ref).resolve()
            if approval.get("runtime_contract_sha256") != digest(runtime_contract):
                blockers.append("APPROVED_RUNTIME_CONTRACT_CHANGED")
        for name in ("output_requirements.json", "run_task.sh", "prepare_runtime.sh", "watch.json", "launch_supervisor.sh"):
            member(root, name)
        if execution.get("schema") != "vasp-practical-execution/v1":
            blockers.append("UNSUPPORTED_EXECUTION_PACKAGE")
        gate = read(member(root, "runtime_gate.json"))
        if (gate.get("schema") != "vasp-practical-runtime-gate/v2"
                or any(gate.get(key) is not False for key in ("user_authorized", "sol_inputs_accepted", "prepare", "launch"))
                or any(gate.get(key) != execution.get(key) for key in ("task_id", "unit_id", "execution_id"))):
            blockers.append("RUNTIME_GATE_NOT_CLOSED_OR_WRONG_IDENTITY")
        checksums = read(member(root, "runtime_checksums.json"))
        for name, expected in checksums.items():
            if digest(member(root, name)) != expected:
                blockers.append("IMMUTABLE_PACKAGE_CHANGED:" + name)
        if (gate.get("plan_sha256") != digest(root / "execution_manifest.json")
                or gate.get("package_checksum_sha256") != digest(root / "runtime_checksums.json")):
            blockers.append("RUNTIME_GATE_PACKAGE_BINDING_MISMATCH")
        required = {"POSCAR", "INCAR", "KPOINTS", "input_manifest.json", "execution_manifest.json",
                    "output_requirements.json", "run_task.sh", "prepare_runtime.sh", "watch.json", "launch_supervisor.sh"}
        if not required.issubset(checksums):
            blockers.append("IMMUTABLE_PACKAGE_SET_INCOMPLETE")
        whitelist = read(member(root, "remote_upload_whitelist.json"))
        if not required.issubset(whitelist.get("files", [])):
            blockers.append("UPLOAD_SET_INCOMPLETE")
        for name in whitelist.get("files", []):
            member(root, name)
    checked = {}
    if not blockers:
        checked = checker(root, tool_root, python, kind, runtime_contract)
        if checked.get("actual_inputs", {}).get("passed") is not True:
            blockers.append("ACTUAL_INPUT_CHECK_FAILED")
        if checked.get("delivery", {}).get("passed") is not True:
            blockers.append("DELIVERY_CHECK_FAILED")
        if kind == "fixed_cell" and checked.get("delivery", {}).get("output_requirements_match") is not True:
            blockers.append("OUTPUT_REQUIREMENTS_CONTRACT_MISMATCH")
    return {"schema": "dft-preparation-handoff/v1", "task_id": plan.get("task_id"), "stage_id": approval.get("stage_id"),
            "status": "LOCAL_PACKAGE_CHECKED" if not blockers else "BLOCKED", "blockers": blockers,
            "scientific_differences": differences, "candidate": str(root), "package_kind": kind,
            "approval_record_ref": str(Path(approval_path).resolve()), "plan_ref": str(Path(plan_path).resolve()),
            "approved_manifest_ref": str(Path(approved_manifest).resolve()),
            "approval_record_sha256": digest(approval_path), "plan_sha256": digest(plan_path),
            "approved_manifest_sha256": digest(approved_manifest), "existing_checks": checked,
            "candidate_manifest_sha256": digest(root / "input_manifest.json"),
            "candidate_execution_manifest_sha256": digest(root / "execution_manifest.json"),
            "candidate_output_requirements_sha256": digest(member(root, "output_requirements.json")) if not blockers else None,
            "execution_authorized": False, "scientific_acceptance": "NOT_AUTHORIZED",
            "limits": ["Approval references require agent verification against actual user and scientific decisions.",
                       "Source frame and geometry scientific suitability remain with the approved source review.",
                       "Remote environment, PAW assembly and restart files require actual startup checks."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "plan", "approval", "approved-manifest", "workspace", "tool-root", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Output exists; preserve the existing handoff")
    try:
        result = audit(args.candidate, args.plan, args.approval, args.approved_manifest, args.workspace, args.tool_root, args.python)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        result = {"schema": "dft-preparation-handoff/v1", "status": "BLOCKED", "blockers": [str(error)],
                  "scientific_differences": [], "candidate": str(args.candidate), "error_type": type(error).__name__,
                  "execution_authorized": False, "scientific_acceptance": "NOT_AUTHORIZED"}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "preparation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 准备与检查交付", "", "状态：" + result["status"], "", "运行包：" + result["candidate"],
             "", "科学设置差异：" + json.dumps(result["scientific_differences"], ensure_ascii=False),
             "", "缺项：" + json.dumps(result["blockers"], ensure_ascii=False),
             "", "本结果仅限本地准备，尚未启动计算；远端条件在启动前核验。", ""]
    (args.output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    if result["blockers"]:
        from issue_report import write_issue
        write_issue(args.output_dir, result.get("task_id"), "BLOCKED", result["blockers"],
                    "候选包未通过准备检查，不能据此部署或启动。", "核对原始回执与批准规格，完成范围内机械修正后重验。",
                    "科学选择或批准范围需改变时由研究者决定；否则暂无。", ["preparation.json"])
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}))
    return 0 if not result["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
