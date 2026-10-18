"""Summarize dated full-stack reports and observed host collaboration capabilities.
Never installs, deploys, launches or changes project instructions.
"""
import argparse
import json
from pathlib import Path
from environment_check import combine
from issue_report import render as render_issues

REQUIRED = {"single": (), "subagent": ("spawn",), "paired": ("thread_identity", "message")}

def summarize(tools, workstation, mode, capabilities):
    if mode not in REQUIRED:
        raise ValueError("Unknown collaboration mode")
    environment = combine(tools, workstation)
    gaps = [{"area": "environment", "id": row["id"], "state": row["state"],
             "reason": row["evidence"], "next_action": row.get("remedy") or "VERIFY",
             "impact": "Full standard environment is not verified"}
            for row in environment["checks"] if row["state"] != "PASS"]
    observed = {}
    for name in REQUIRED[mode]:
        row = capabilities.get(name, {})
        # Positive capability needs an actual observation basis; configuration is not evidence.
        passed = row.get("state") == "PASS" and isinstance(row.get("basis"), str) and bool(row["basis"].strip())
        observed[name] = "PASS" if passed else "UNVERIFIED"
        if not passed:
            gaps.append({"area": "collaboration", "id": name, "state": "UNVERIFIED",
                         "reason": row, "impact": "Selected mode is not verified",
                         "next_action": "Verify host capability or discuss a different mode"})
    return {"schema": "dft-project-setup/v1", "environment": environment,
            "mode": mode, "observed_capabilities": observed,
            "status": "READY_FOR_PLAN_DISCUSSION" if not gaps else "ACTION_REQUIRED",
            "issues": gaps, "execution_authorized": False,
            "limitations": ["Dated reports are not live checks", "No deployment or scientific approval implied"]}

def problem_report(value):
    if not value["issues"]:
        return None
    return {"schema": "dft-issue-report/v1", "task_id": "PROJECT_SETUP", "status": "BLOCKED",
            "issues": [{"reason": row["id"] + " / " + row["state"] + ": " + json.dumps(row["reason"], ensure_ascii=False),
                        "impact": row["impact"], "agent_next_action": row["next_action"],
                        "user_decision": "Confirm a supported mode if changing collaboration" if row["area"] == "collaboration" else "Provide missing environment evidence or authorize the specific remediation when needed",
                        "evidence_refs": ["setup.json"]} for row in value["issues"]]}

def brief(value):
    lines = ["# 项目接入回执", "", "状态：" + value["status"], "协作模式：" + value["mode"],
             "", "已核实项见 environment.checks；未核实或缺失项如下："]
    for issue in value["issues"]:
        lines.append("- " + issue["id"] + " / " + issue["state"] + "：" + issue["impact"] + "；下一步：" + issue["next_action"])
    lines += ["", "不授予执行权限；沿用原始报告的主机和观察时间，变化后重新核实。"]
    return "\n".join(lines) + "\n"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-report", type=Path)
    parser.add_argument("--workstation-report", type=Path)
    parser.add_argument("--mode", required=True, choices=REQUIRED)
    parser.add_argument("--capabilities", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding="utf-8-sig")) if path else None
    value = summarize(read(args.tools_report), read(args.workstation_report), args.mode, read(args.capabilities) or {})
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "setup.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "README.md").write_text(brief(value), encoding="utf-8")
    issues = problem_report(value)
    if issues:
        (args.output_dir / "issues.json").write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.output_dir / "ISSUES.md").write_text(render_issues(issues), encoding="utf-8")
    return 0 if value["status"] == "READY_FOR_PLAN_DISCUSSION" else 1

if __name__ == "__main__":
    raise SystemExit(main())
