"""Bounded status handoff over existing workstation evidence; never monitors.

The existing job_watch writes workstation state. Existing quick-status performs
at most one read-only query only with explicit authority. This layer reports and
ends, or hands a terminal observation to authorized recovery/analysis. It does
not launch, deploy, restart, schedule, recover files or claim scientific success.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from intake import text
from preparation import digest

TERMINAL = {"COMPLETED_PENDING_REVIEW", "FAILED_OR_INCOMPLETE", "SUPERVISOR_ERROR"}
VERIFY = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from progress_evidence import _load_quick_status_identity, _quick_status_canonical
expected = _load_quick_status_identity(sys.argv[2])
snapshot = json.loads(Path(sys.argv[3]).read_text(encoding='utf-8-sig'))
state, consistency, reasons = _quick_status_canonical(snapshot)
print(json.dumps({'expected':expected,'state':state,'consistency':consistency,'reasons':reasons}))
'''


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp needs a timezone")
    return parsed


def permitted(authority, expected, action, trigger, manifest_sha=None):
    if (not isinstance(authority, dict) or authority.get("schema") != "dft-execution-authority/v1"
            or not all(text(expected.get(key)) and authority.get(key) == expected[key] for key in ("task_id", "unit_id", "execution_id"))):
        return False
    confirmation = authority.get("user_confirmation", {})
    if not isinstance(confirmation, dict) or not all(text(confirmation.get(key)) for key in ("reference", "quote")):
        return False
    if not text(manifest_sha) or authority.get("manifest_sha256") != manifest_sha:
        return False
    if trigger == "scheduled" and not text(authority.get("schedule_reference")):
        return False
    actions = authority.get("allowed_actions")
    return isinstance(actions, list) and all(isinstance(value, str) for value in actions) and action in actions


def query_once(manifest, tool_root, python, authority, expected, trigger, runner=subprocess.run):
    if trigger not in {"startup", "user", "scheduled"}:
        raise ValueError("Explicit startup/user/already-scheduled trigger required")
    if not permitted(authority, expected, "QUERY", trigger, digest(manifest)):
        raise PermissionError("Query requires existing explicit, manifest-bound authority")
    argv = [str(python), "-B", str(Path(tool_root) / "progress_evidence.py"), "quick-status", "--manifest", str(manifest)]
    # No retry, sleep, wait loop or notification. The existing SSH call is 30 s.
    try:
        completed = runner(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
        value = json.loads(completed.stdout)
        if value.get("schema") != "vasp-quick-status/v1":
            raise ValueError("Unsupported quick-status result")
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return {"schema": "vasp-quick-status/v1", "query_status": "PARTIAL", "canonical_status": "UNKNOWN",
                "state_consistency": "UNKNOWN", "identity": expected, "observed_local_utc": datetime.now(timezone.utc).isoformat(),
                "ssh": {"attempted": True, "exit_code": None, "timed_out": isinstance(error, subprocess.TimeoutExpired)},
                "reasons": ["Single query failed: " + type(error).__name__]}


def evaluate(snapshot, verified, trigger, authority=None, *, manifest_sha=None, now=None):
    if trigger not in {"startup", "user", "scheduled"}:
        raise ValueError("Explicit check trigger required")
    current = now or datetime.now(timezone.utc)
    expected = verified["expected"]
    problems = []
    identity = snapshot.get("identity", {})
    if snapshot.get("schema") != "vasp-quick-status/v1":
        problems.append("UNSUPPORTED_SNAPSHOT")
    for key in ("task_id", "unit_id", "execution_id", "host", "remote_batch_dir", "remote_case_dir", "tmux_session"):
        if not expected.get(key) or identity.get(key) != expected.get(key):
            problems.append("IDENTITY_MISMATCH:" + key)
    observed = snapshot.get("remote_observed_utc") or snapshot.get("observed_local_utc")
    try:
        if stamp(observed) > current:
            problems.append("FUTURE_OBSERVATION")
    except (ValueError, TypeError, AttributeError):
        problems.append("OBSERVATION_TIME_UNKNOWN")
    state = verified.get("state")
    if (verified.get("consistency") != "AGREED" or snapshot.get("canonical_status") != state
            or snapshot.get("state_consistency") != "AGREED"):
        problems.append("STATE_NOT_CORROBORATED")
    ssh = snapshot.get("ssh", {})
    if ssh.get("exit_code") != 0 or ssh.get("timed_out") is not False:
        problems.append("QUERY_TRANSPORT_INCOMPLETE")
    action = "REPORT_AND_END"
    result_state = "UNKNOWN" if problems else state
    startup = "NOT_APPLICABLE"
    sources = snapshot.get("sources", {})
    status_source, timing_source = sources.get("status_json", {}), sources.get("run_timing", {})
    processes, tmux = snapshot.get("processes", []), snapshot.get("tmux", {})
    timing_file = snapshot.get('files', {}).get('run_timing.txt', {})
    timing_confirmed_missing = (timing_file.get('reason') == 'missing'
                                and timing_file.get('read_before', {}).get('present') is False
                                and timing_file.get('read_bytes') == 0
                                and timing_file.get('read_error') is None)
    # Static runners may not create timing until exit. Keep the canonical full
    # state UNKNOWN, but allow an explicitly limited startup observation from a
    # strongly bound supervisor, live exact PIDs/session and post-start output.
    startup_only = (trigger == "startup" and problems == ["STATE_NOT_CORROBORATED"]
                    and state == "UNKNOWN" and snapshot.get("canonical_status") == "UNKNOWN"
                    and status_source.get("identity_binding", {}).get("state") == "MATCH"
                    and status_source.get("raw_status") == "RUNNING"
                    and timing_source.get("available") is False
                    and (timing_source.get("identity_binding", {}).get("state") != "MISMATCH" or timing_confirmed_missing)
                    and processes and all(row.get("state") == "PRESENT" for row in processes)
                    and tmux.get("state") == "PRESENT" and tmux.get("pane_dead") is False)
    if trigger == "startup" and ((not problems and state in {"STARTING", "RUNNING"}) or startup_only):
        startup = "PENDING"
        sources, progress = snapshot.get("sources", {}), snapshot.get("progress", {})
        started = sources.get("status_json", {}).get("started_utc")
        evidence = []
        oszicar, stdout = progress.get("oszicar", {}), progress.get("stdout", {})
        for role, rows in (("OSZICAR", oszicar.get("tail_visible_electronic_rows") or oszicar.get("last_visible_complete_ionic_row")),
                           ("vasp.stdout", stdout.get("electronic_rows") or stdout.get("ionic_rows"))):
            file = snapshot.get("files", {}).get(role, {})
            modified = file.get("read_after", {}).get("mtime_utc")
            try:
                if rows and file.get("stable") is True and stamp(started) <= stamp(modified) <= stamp(observed):
                    evidence.append(role)
            except (ValueError, TypeError, AttributeError):
                pass
        tails = (progress.get("stderr", {}).get("tail_lines", []) + stdout.get("tail_lines", [])
                 + [row.get("raw", "") for row in progress.get("outcar", {}).get("marker_lines", [])])
        fatal = any(re.search(r"fatal error|segmentation fault|mpi_abort|internal error", line, re.I) for line in tails if isinstance(line, str))
        if (state == "RUNNING" or startup_only) and evidence and not fatal:
            startup = "STARTED_WITH_OUTPUT_EVIDENCE"
        elif fatal:
            startup = "STARTUP_ERROR_EVIDENCE"
        elif any(isinstance(line, str) and line.strip().lower() == 'entering main loop' for line in stdout.get('tail_lines', [])):
            output_file = snapshot.get('files', {}).get('vasp.stdout', {})
            try:
                if (output_file.get('stable') is True
                        and stamp(started) <= stamp(output_file.get('read_after', {}).get('mtime_utc')) <= stamp(observed)):
                    startup = 'INITIALIZED_PENDING_PROGRESS'
            except (ValueError, TypeError, AttributeError):
                pass
        action = "REPORT_STARTUP_AND_END"
    elif not problems and state in TERMINAL:
        if (permitted(authority, expected, "RECOVER", trigger, manifest_sha)
                and permitted(authority, expected, "ANALYZE", trigger, manifest_sha)):
            action = "HANDOFF_TO_RECOVERY_AND_ANALYSIS"
        else:
            action = "REPORT_TERMINAL_AND_AUTHORITY_GAP"
    return {"schema": "dft-execution-handoff/v1", "identity": expected, "trigger": trigger,
            "observed_utc": observed, "state": result_state, "startup_result": startup, "next_action": action,
            "blockers": problems, "source_limitations": snapshot.get("reasons", []),
            "end_agent_turn": action != "HANDOFF_TO_RECOVERY_AND_ANALYSIS", "continuous_monitoring": False,
            "new_schedule_created": False, "recovery_performed": False, "scientific_acceptance": "NOT_EVALUATED",
            "startup_evidence_only": bool(startup_only),
            "limit": "Dated observation only; no claim of current state after the recorded time or scientific convergence"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "tool-root", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--query", action="store_true")
    parser.add_argument("--authority", type=Path)
    parser.add_argument("--trigger", required=True, choices=["startup", "user", "scheduled"])
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    if args.output_dir.exists() or (args.query == bool(args.snapshot)):
        parser.error("Use a new output directory and exactly one snapshot or explicit query")
    authority = json.loads(args.authority.read_text(encoding="utf-8-sig")) if args.authority else None
    if args.query:
        # Read and validate the manifest identity locally before any query.
        code = "import json,sys;sys.path.insert(0,sys.argv[1]);from progress_evidence import _load_quick_status_identity;print(json.dumps(_load_quick_status_identity(sys.argv[2])))"
        child = subprocess.run([str(args.python), "-B", "-c", code, str(args.tool_root.resolve()), str(args.manifest.resolve())], capture_output=True, text=True, timeout=10, check=True)
        expected = json.loads(child.stdout)
        snapshot = query_once(args.manifest, args.tool_root, args.python, authority, expected, args.trigger)
    else:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    snapshot_path = args.output_dir / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    child = subprocess.run([str(args.python), "-B", "-c", VERIFY, str(args.tool_root.resolve()), str(args.manifest.resolve()), str(snapshot_path.resolve())],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=True)
    result = evaluate(snapshot, json.loads(child.stdout), args.trigger, authority, manifest_sha=digest(args.manifest))
    (args.output_dir / "execution_handoff.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result["startup_result"] == "STARTED_WITH_OUTPUT_EVIDENCE":
        summary = "已观察到计算实际推进的输出，当前启动核验结束。"
        if result["startup_evidence_only"]:
            summary += "运行计时记录尚未就绪，完整状态仍待核实。"
    elif result["startup_result"] == "STARTUP_ERROR_EVIDENCE":
        summary = "启动输出中发现错误证据，需要核实原因。"
    elif result['startup_result'] == 'INITIALIZED_PENDING_PROGRESS':
        summary = '已观察到初始化完成并进入主循环；尚无完整电子步证据，不能据此确认收敛或正常终态。本轮启动核验结束，不持续等待。'
    elif result["startup_result"] == "PENDING":
        summary = "启动证据尚不足，本轮报告缺口后结束，不持续等待。"
    elif result["state"] in TERMINAL:
        summary = "已观察到计算终态，按已有授权处理回收与分析。"
    elif result["state"] == "RUNNING":
        summary = "本次核实时计算在运行；报告后结束本轮。"
    else:
        summary = "现有证据不能完整判断计算状态，需要保留未知。"
    lines = ["# 执行与取证交付", "", summary, "", "核实时间：" + str(result["observed_utc"]), "", "观察状态：" + result["state"],
             "", "启动核验：" + result["startup_result"], "", "下一动作：" + result["next_action"],
             "", "仅代表记录时刻；本工具不持续盯守，也未回收输出或作科学接受。", ""]
    (args.output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    if result["blockers"] or result["startup_result"] in {"PENDING", "STARTUP_ERROR_EVIDENCE"} or result["state"] == "UNKNOWN":
        from issue_report import write_issue
        reasons = result["blockers"] or ["启动核验：" + str(result["startup_result"]) + "；状态：" + result["state"]]
        write_issue(args.output_dir, result["identity"].get("task_id"), "UNKNOWN", reasons,
                    "本次观察不足以确认正常推进或完整运行状态。", "报告已有证据后结束；仅按请求或明确安排再次检查，不自动重启。",
                    "需要扩大诊断、修复或改变执行范围时由用户决定。", ["execution_handoff.json", "snapshot.json"])
    print(json.dumps({"state": result["state"], "next_action": result["next_action"]}))
    return 1 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
