"""Create continuation context from an existing successful stage receipt, not a chosen stage."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

def checkpoint(receipt, workspace, task_id, writer, output):
    root = Path(workspace).resolve(strict=True)
    source = Path(receipt).resolve(strict=True); source.relative_to(root)
    target = Path(output).resolve(); target.relative_to(root)
    if not task_id or not writer:
        raise ValueError("Task identity and responsible writer required")
    raw = source.read_bytes(); value = json.loads(raw.decode("utf-8-sig"))
    identity = value.get("identity", {})
    if value.get("task_id", identity.get("task_id")) != task_id:
        raise ValueError("Receipt task identity mismatch")
    schema = value.get("schema")
    if schema == "dft-preparation-handoff/v1" and value.get("status") == "LOCAL_PACKAGE_CHECKED" and value.get("blockers") == []:
        stage = "PREPARED"
    elif schema == "dft-analysis-facts/v1" and value.get("backend_status") == "SUCCEEDED":
        stage = "ANALYZED"
    elif schema == "dft-recovery-handoff/v1" and value.get("recovery_performed") is True and value.get("status") in {"RECOVERED_PARTIAL", "RECOVERED_COMPLETE"}:
        stage = "RECOVERED"
    elif schema == "dft-execution-handoff/v1" and value.get("startup_result") == "STARTED_WITH_OUTPUT_EVIDENCE" and not value.get("blockers"):
        stage = "RUNNING"
    else:
        raise ValueError("Receipt cannot establish a supported continuation stage")
    evidence = [{"path": source.relative_to(root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}]
    for field, ref in value.items():
        if not field.endswith("_ref") or ref is None:
            continue
        hash_field = field[:-4] + "_sha256"
        expected = value.get(hash_field)
        if expected is None:
            continue
        path = (root / ref).resolve(strict=True)
        path.relative_to(root)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("Referenced source changed: " + field)
        evidence.append({"path": path.relative_to(root).as_posix(), "sha256": actual})
    recovery = value if schema == "dft-recovery-handoff/v1" else None
    if schema == "dft-analysis-facts/v1" and value.get("recovery_ref"):
        recovery = json.loads((root / value["recovery_ref"]).read_text(encoding="utf-8-sig"))
    if recovery:
        for row in recovery.get("files", {}).values():
            path = (root / row["path"]).resolve(strict=True)
            path.relative_to(root)
            data = path.read_bytes()
            if len(data) != row.get("bytes") or hashlib.sha256(data).hexdigest() != row.get("sha256"):
                raise ValueError("Recovered raw evidence changed")
            evidence.append({"path": path.relative_to(root).as_posix(), "sha256": row["sha256"]})
    record = {"schema": "dft-continuation/v1", "task_id": task_id, "writer": writer,
              "stage": stage, "recorded_at": datetime.now(timezone.utc).isoformat(),
              "approval_scope": None,
              "evidence": evidence,
              "limitations": ["Receipt stage only; revalidate referenced inputs and approvals before acting", "No scientific acceptance or execution authority granted"]}
    with target.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return record

def link_task_card(card, record, workspace):
    """Update only a continuation subsection within the selected task slot."""
    from analysis import update_card
    root = Path(workspace).resolve(strict=True)
    card = Path(card)
    if card.is_symlink(): raise ValueError("Linked task card refused")
    card.resolve().relative_to(root)
    record = Path(record).resolve(strict=True); record.relative_to(root)
    value = json.loads(record.read_text(encoding="utf-8-sig"))
    task_id = value["task_id"]
    begin = "<!-- AGENT_TASK_CARD_BEGIN:" + task_id + " -->"
    end = "<!-- AGENT_TASK_CARD_END:" + task_id + " -->"
    content = ""
    if card.exists():
        old = card.read_text(encoding="utf-8-sig")
        if old.count(begin)!=1 or old.count(end)!=1 or old.index(begin)>old.index(end):
            raise ValueError("Existing task card requires a matching task slot")
        content = old.split(begin,1)[1].split(end,1)[0].strip()
    start, stop = "<!-- CONTINUATION_BEGIN -->", "<!-- CONTINUATION_END -->"
    block = start + "\n继续记录：[" + record.name + "](" + record.as_posix() + ")\n记录阶段：" + value["stage"] + "；写入者：" + value["writer"] + "\n历史观察不代表实时状态；恢复前核对证据与授权。\n" + stop
    if start in content or stop in content:
        if content.count(start)!=1 or content.count(stop)!=1 or content.index(start)>content.index(stop):
            raise ValueError("Conflicting continuation card slots")
        content = content[:content.index(start)] + block + content[content.index(stop)+len(stop):]
    else:
        content += "\n\n" + block
    update_card(card, task_id, content.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("receipt", "workspace", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--writer", required=True)
    parser.add_argument("--task-card", type=Path)
    args = parser.parse_args()
    checkpoint(args.receipt, args.workspace, args.task_id, args.writer, args.output)
    if args.task_card:
        link_task_card(args.task_card, args.output, args.workspace)

if __name__ == "__main__":
    main()
