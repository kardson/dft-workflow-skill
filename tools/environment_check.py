"""Read-only standard-stack inspection. Never launches VASP or connects remotely.

Run tools on the agent host, workstation on the VASP host, then combine reports.
The fixed checklist is intentionally independent of workstation-specific profiles.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

SCHEMA = "dft-standard-environment/v1"
PACKAGES = {
    "science": {"numpy": "numpy", "ase": "ase", "pymatgen": "pymatgen.core",
                "matplotlib": "matplotlib"},
    "analysis": {"custodian": "custodian", "py4vasp": "py4vasp",
                 "sumo": "sumo", "MacroDensity": "macrodensity"},
}
TOOL_FILES = ("vasp_input_generator.py", "vasp_executor.py", "static_delivery_builder.py",
              "progress_evidence.py", "job_watch.py", "vasp_operations.py",
              "result_registry.py", "scientific_helpers.py", "analysis_addons.py")
SUPPORT_FILES = ("practical_execution_builder.py", "practical_runtime.py", "progress_snapshot.py",
                 "vasp_execution_state.py", "static_delivery_check.py", "property_plan.py")
WORKSTATION_IDS = ("vasp", "mpi", "vaspkit", "session", "paw", "workdir",
                   "cpu", "memory", "storage", "compiled_features", "runtime_interfaces")
TOOLS_IDS = tuple("package:" + name for group in PACKAGES.values() for name in group) + ("dependency_compatibility", "tool_interfaces")


def now():
    return datetime.now(timezone.utc).isoformat()


def item(name, state, evidence, remedy=None):
    return {"id": name, "state": state, "evidence": evidence, "remedy": remedy}


def report(scope, checks, **extra):
    states = {row["state"] for row in checks}
    status = "MISSING" if "MISSING" in states else "UNVERIFIED" if "UNVERIFIED" in states else "COMPLETE"
    return {"schema": SCHEMA, "scope": scope, "observed_at": now(),
            "host": platform.node(), "platform": platform.platform(), "status": status,
            "checks": checks, "effects": {"vasp_launched": False, "remote_calls": False,
                                         "installed": False, "monitor_started": False}, **extra}


PACKAGE_PROBE = '''import importlib, importlib.metadata as m, json, sys
result = {}
for name, module in json.loads(sys.argv[1]).items():
    try:
        imported = importlib.import_module(module)
        distributions = [name, 'py4vasp-core'] if name == 'py4vasp' else [name]
        version = None
        for distribution in distributions:
            try:
                version = m.version(distribution)
                break
            except m.PackageNotFoundError:
                pass
        result[name] = {'state': 'PASS', 'version': version, 'module': module}
    except Exception as error:
        result[name] = {'state': 'MISSING', 'error_type': type(error).__name__, 'module': module}
print('DFT_ENV_JSON:' + json.dumps(result))
'''


def packages(python, group, runner=subprocess.run):
    names = PACKAGES[group]
    executable = Path(python)
    if not executable.is_file():
        return [item("package:" + name, "MISSING", {"python": str(executable)}, "INSTALL_OR_CONFIGURE") for name in names]
    try:
        # -I avoids user PYTHONPATH and user-site. No package installation or shell.
        child = runner([str(executable), "-I", "-B", "-c", PACKAGE_PROBE, json.dumps(names)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        encoded = [line[len("DFT_ENV_JSON:"):] for line in child.stdout.splitlines() if line.startswith("DFT_ENV_JSON:")]
        if child.returncode != 0 or len(encoded) != 1:
            raise ValueError("Probe did not return one result")
        values = json.loads(encoded[0])
        if set(values) != set(names) or any(v.get("state") not in {"PASS", "MISSING"} for v in values.values()):
            raise ValueError("Probe result contract differs")
        return [item("package:" + name, values[name]["state"],
                     {"python": str(executable.resolve()), **values[name]},
                     None if values[name]["state"] == "PASS" else "INSTALL_OR_CONFIGURE") for name in names]
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as error:
        return [item("package:" + name, "UNVERIFIED", {"error_type": type(error).__name__, "python": str(executable)}, "VERIFY") for name in names]


def tools_probe(root, science_python, analysis_python):
    checks = packages(science_python, "science") + packages(analysis_python, "analysis")
    root = Path(root)
    missing = [name for name in (*TOOL_FILES, *SUPPORT_FILES) if not (root / name).is_file()]
    if not any((root / name).is_file() for name in ("result_analysis.py", "vasp_result_analysis.py")):
        missing.append("result_analysis.py or vasp_result_analysis.py")
    compatibility = []
    for python in dict.fromkeys((str(science_python), str(analysis_python))):
        try:
            child = subprocess.run([python, "-I", "-B", "-m", "pip", "check"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=30)
            compatibility.append({"python": python, "exit_code": child.returncode,
                                  "diagnostic": (child.stdout + child.stderr)[-4000:]})
        except (OSError, subprocess.TimeoutExpired) as error:
            compatibility.append({"python": python, "exit_code": None, "error_type": type(error).__name__})
    checks.append(item("dependency_compatibility", "PASS" if all(row["exit_code"] == 0 for row in compatibility) else "UNVERIFIED",
                       {"pip_check": compatibility}, None if all(row["exit_code"] == 0 for row in compatibility) else "VERIFY_OR_CONFIGURE"))
    interfaces = []
    if not missing:
        for name in TOOL_FILES:
            try:
                child = subprocess.run([str(science_python), "-B", str((root / name).resolve()), "--help"],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                interfaces.append({"file": name, "exit_code": child.returncode})
            except (OSError, subprocess.TimeoutExpired) as error:
                interfaces.append({"file": name, "exit_code": None, "error_type": type(error).__name__})
    state = "MISSING" if missing else "PASS" if all(row["exit_code"] == 0 for row in interfaces) else "UNVERIFIED"
    checks.append(item("tool_interfaces", state,
                       {"tool_root": str(root.resolve()), "missing_files": missing, "help_probes": interfaces,
                        "inspection": "CLI help smoke tests; workstation adapter validation is separate"},
                       None if state == "PASS" else "INSTALL_OR_CONFIGURE" if missing else "VERIFY"))
    return report("tools", checks)


def program(name, supplied, alternatives):
    found = supplied or next((shutil.which(value) for value in alternatives if shutil.which(value)), None)
    path = Path(found) if found else None
    exists = path is not None and path.is_file() and os.access(path, os.X_OK)
    identity = {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns} if exists else None
    return item(name, "PASS" if exists else "MISSING", {"executable": str(path.resolve()) if exists else found, "file_identity": identity,
                "inspection": "executable availability; no calculation launched"}, None if exists else "INSTALL_OR_CONFIGURE")


def memory_bytes():
    try:
        if os.name == "nt":
            import ctypes
            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in
                    ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
            value = Memory()
            value.length = ctypes.sizeof(value)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
                return None
            return value.total
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


def workstation_probe(vasp=None, mpi=None, vaspkit=None, session=None, paw=None, workdir="."):
    checks = [program("vasp", vasp, ("vasp_std",)), program("mpi", mpi, ("mpirun", "mpiexec")),
              program("vaspkit", vaspkit, ("vaspkit",)), program("session", session, ("tmux",))]
    paw_root = Path(paw) if paw else None
    available = paw_root is not None and paw_root.is_dir() and os.access(paw_root, os.R_OK)
    checks.append(item("paw", "UNVERIFIED" if available else "MISSING",
                       {"root": str(paw_root) if paw_root else None,
                        "inspection": "directory availability only; family, provenance and components require metadata review; no POTCAR read"},
                       "VERIFY" if available else "CONFIGURE"))
    directory = Path(workdir)
    writable = directory.is_dir() and os.access(directory, os.R_OK | os.W_OK | os.X_OK)
    checks.append(item("workdir", "PASS" if writable else "MISSING", {"path": str(directory.resolve()),
                       "inspection": "permission bits/access only; no write test"}, None if writable else "CONFIGURE"))
    cpu, memory = os.cpu_count(), memory_bytes()
    checks.extend([item("cpu", "UNVERIFIED", {"logical_cpus": cpu, "physical_cores": None,
                   "limit": "physical core allocation and task-specific capacity must be reviewed"}, "VERIFY"),
                   item("memory", "PASS" if memory else "UNVERIFIED", {"physical_memory_bytes": memory}, None if memory else "VERIFY")])
    try:
        usage = shutil.disk_usage(directory)
        checks.append(item("storage", "PASS" if usage.free > 0 else "MISSING", {"free_bytes": usage.free,
                           "limit": "task-specific storage budget is not established"}, None if usage.free else "CONFIGURE"))
    except OSError:
        checks.append(item("storage", "UNVERIFIED", {"path": str(directory)}, "VERIFY"))
    checks.extend([item("compiled_features", "UNVERIFIED", {"required": ["VASP HDF5 output"],
                   "limit": "installed HDF5 library does not establish actual VASP build features"}, "VERIFY_BUILD_OR_RECOMPILE_IF_MISSING"),
                   item("runtime_interfaces", "UNVERIFIED", {"required": ["MPI compatibility", "dynamic libraries", "VASPKIT interfaces",
                   "workstation state writer", "status reader", "output recovery"],
                   "limit": "program availability does not prove deployed interfaces work"}, "VERIFY_OR_CONFIGURE")])
    return report("workstation", checks)


def validate_report(value, scope):
    if scope not in {"tools", "workstation"} or not value.get("host"):
        raise ValueError("Explicit tools/workstation scope and source host required")
    expected = TOOLS_IDS if scope == "tools" else WORKSTATION_IDS
    if value.get("schema") != SCHEMA or value.get("scope") != scope:
        raise ValueError("Wrong report schema or scope")
    stamp = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    rows = value.get("checks", [])
    if len(rows) != len(expected) or {row.get("id") for row in rows} != set(expected):
        raise ValueError("Missing, duplicate or unknown checklist entries")
    if any(row.get("state") not in {"PASS", "MISSING", "UNVERIFIED"} or not row.get("evidence") for row in rows):
        raise ValueError("Invalid checklist evidence")
    if stamp > datetime.now(timezone.utc):
        raise ValueError("Future observations are invalid")
    return value


def apply_review(value, review, root):
    """Attach a dated agent/operator review, without claiming it is a new probe.

This is the explicit evidence bridge for build/runtime facts that cannot be
established without an authorized workstation validation. It cannot clear a
detected missing dependency or silently change an observation's identity.
"""
    validate_report(value, value.get("scope"))
    if (review.get("schema") != "dft-environment-review/v1" or review.get("report_observed_at") != value["observed_at"]
            or review.get("report_host") != value["host"] or not review.get("reviewed_by")):
        raise ValueError("Review must identify the exact observed report and reviewer")
    reviewed_at = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
    if reviewed_at.tzinfo is None or reviewed_at > datetime.now(timezone.utc) or reviewed_at < datetime.fromisoformat(value["observed_at"]):
        raise ValueError("Invalid review timestamp")
    updates = review.get("checks", [])
    rows = {row["id"]: dict(row) for row in value["checks"]}
    seen = set()
    for update in updates:
        key = update["id"]
        if key in seen or key not in rows or rows[key]["state"] != "UNVERIFIED":
            raise ValueError("Review can resolve only unique, unverified entries; reprobe missing software after repair")
        seen.add(key)
        if update.get("state") not in {"PASS", "MISSING", "UNVERIFIED"} or not update.get("basis"):
            raise ValueError("Review needs a state and explicit factual basis")
        if update["state"] != "PASS" and not update.get("remedy"):
            raise ValueError("Unresolved or missing items require a concrete next action")
        refs = update.get("evidence_refs", [])
        if not refs:
            raise ValueError("Review requires existing evidence files")
        root = Path(root).resolve()
        for ref in refs:
            path = (root / ref).resolve()
            path.relative_to(root)
            if not path.is_file():
                raise ValueError("Review evidence does not exist")
        rows[key].update(state=update["state"], remedy=update.get("remedy"),
                         evidence={"original_observation": rows[key]["evidence"], "review": update,
                                   "reviewed_by": review["reviewed_by"], "reviewed_at": review["reviewed_at"],
                                   "evidence_root": str(root)})
    return report(value["scope"], list(rows.values()), host=value["host"],
                  platform=value.get("platform"), observed_at=value["observed_at"],
                  review_note="Dated operator/agent review; no new workstation probe")


def combine(tools, workstation):
    checks = []
    for scope, value, expected in (("tools", tools, TOOLS_IDS), ("workstation", workstation, WORKSTATION_IDS)):
        if value is None:
            checks.extend(item(name, "UNVERIFIED", {"scope": scope, "reason": "report not provided"}, "VERIFY") for name in expected)
        else:
            validate_report(value, scope)
            # Imported reports are dated observations, never a live connection check.
            checks.extend({**row, "source_scope": scope, "source_observed_at": value["observed_at"],
                           "source_host": value.get("host"), "source_platform": value.get("platform")} for row in value["checks"])
    return report("combined", checks, scope_note="Full standard stack, not task readiness or scientific acceptance; imported evidence is not a live check")


def brief(value):
    label = {"COMPLETE": "标准工作流环境完整", "MISSING": "标准工作流环境存在缺项", "UNVERIFIED": "标准工作流环境仍有待核实项"}[value["status"]]
    if value["scope"] != "combined":
        label = ("工具侧" if value["scope"] == "tools" else "工作站侧") + "检查：" + value["status"] + "；完整环境需合并两侧证据后判断。"
    lines = ["# 环境检查结果", "", label, "", "核实时间（UTC）：" + value["observed_at"], ""]
    lines.extend("- " + row["id"] + ": " + row["state"] + "; " + str(row.get("remedy")) for row in value["checks"] if row["state"] != "PASS")
    lines.extend(["", "完整安装与特定研究的科学适用性分别判断；工作站报告按其来源时间阅读。", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("tools")
    local.add_argument("--tool-root", required=True, type=Path)
    local.add_argument("--science-python", required=True, type=Path)
    local.add_argument("--analysis-python", required=True, type=Path)
    host = commands.add_parser("workstation")
    for name in ("vasp", "mpi", "vaspkit", "session", "paw", "workdir"):
        host.add_argument("--" + name, default="." if name == "workdir" else None)
    merged = commands.add_parser("combine")
    merged.add_argument("--tools-report", type=Path)
    merged.add_argument("--workstation-report", type=Path)
    reviewed = commands.add_parser("review")
    reviewed.add_argument("--report", required=True, type=Path)
    reviewed.add_argument("--review", required=True, type=Path)
    reviewed.add_argument("--evidence-root", required=True, type=Path)
    for command in (local, host, merged, reviewed):
        command.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Output directory already exists; preserve previous evidence")
    if args.command == "tools":
        value = tools_probe(args.tool_root, args.science_python, args.analysis_python)
    elif args.command == "workstation":
        value = workstation_probe(**{name: getattr(args, name) for name in ("vasp", "mpi", "vaspkit", "session", "paw", "workdir")})
    elif args.command == "combine":
        read = lambda path: json.loads(path.read_text(encoding="utf-8-sig")) if path else None
        value = combine(read(args.tools_report), read(args.workstation_report))
    else:
        value = apply_review(json.loads(args.report.read_text(encoding="utf-8-sig")),
                             json.loads(args.review.read_text(encoding="utf-8-sig")), args.evidence_root)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "environment.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "README.md").write_text(brief(value), encoding="utf-8")
    print(json.dumps({"scope": value["scope"], "status": value["status"], "report": str(args.output_dir / "environment.json")}, ensure_ascii=True))
    return 0 if value["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
