"""Common evidence-gap delivery; does not authorize or execute remedies."""
import argparse
import json
from pathlib import Path

def validate(value):
    if value.get("schema") != "dft-issue-report/v1" or not value.get("task_id"):
        raise ValueError("Issue report schema and task identity required")
    if value.get("status") not in {"BLOCKED", "PARTIAL", "FAILED", "UNKNOWN"}:
        raise ValueError("Invalid issue status")
    if not isinstance(value.get("issues"), list) or not value["issues"]:
        raise ValueError("Concrete issues required")
    for issue in value["issues"]:
        for field in ("reason", "impact", "agent_next_action", "user_decision"):
            if not isinstance(issue.get(field), str) or not issue[field].strip():
                raise ValueError("Missing issue field: " + field)
        refs = issue.get("evidence_refs")
        if not isinstance(refs, list) or any(not isinstance(r, str) or not r.strip() for r in refs):
            raise ValueError("Explicit evidence references required; empty means no evidence")
    return value

def render(value):
    validate(value)
    lines = ["# 当前问题与下一步", "", value["task_id"] + " / " + value["status"]]
    for issue in value["issues"]:
        for field, label in (("reason", "发生了什么"), ("impact", "影响"), ("agent_next_action", "Agent 下一步"), ("user_decision", "用户需要决定")):
            lines.append(label + "：" + issue[field])
        lines.append("证据：" + (", ".join(issue["evidence_refs"]) or "尚无；不能作已验证判断"))
        lines.append("")
    lines.append("下一步建议不构成新的执行授权。")
    return "\n".join(lines) + "\n"

def write_issue(output, task_id, status, reasons, impact, next_action, decision, evidence_refs):
    """Write sidecar guidance beside existing evidence without changing that evidence."""
    value = {"schema": "dft-issue-report/v1", "task_id": task_id or "UNRESOLVED_TASK",
             "status": status, "issues": [{"reason": str(reason), "impact": impact,
             "agent_next_action": next_action, "user_decision": decision,
             "evidence_refs": evidence_refs} for reason in reasons]}
    text = render(value)
    output = Path(output)
    # Existing evidence remains authoritative; never overwrite an older issue sidecar.
    for name, content in (("issues.json", json.dumps(value, ensure_ascii=False, indent=2)), ("ISSUES.md", text)):
        with (output / name).open("x", encoding="utf-8") as stream:
            stream.write(content)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    text = render(json.loads(args.report.read_text(encoding="utf-8-sig")))
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(text)

if __name__ == "__main__":
    main()
