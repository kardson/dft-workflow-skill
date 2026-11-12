"""Create continuation context from an existing successful stage receipt, not a chosen stage."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

NAVIGATION_ROLES = {'research', 'plan', 'approval', 'preparation', 'execution', 'observation', 'facts', 'review', 'blockers'}
UPDATE_REASONS = {'task_transition', 'authority_change', 'delivery', 'new_evidence', 'blocker_change', 'responsibility_change'}


def navigation_context(path, root, task_id):
    """Bind explicitly listed sources; role labels are claims, never approvals."""
    source = Path(path).resolve(strict=True); source.relative_to(root)
    raw = source.read_bytes()
    value = json.loads(raw.decode('utf-8-sig'))
    if value.get('schema') != 'dft-task-navigation/v1' or value.get('task_id') != task_id:
        raise ValueError('Navigation schema/task identity mismatch')
    if value.get('update_reason') not in UPDATE_REASONS:
        raise ValueError('Explicit navigation update reason required')
    for field in ('next_action', 'next_action_owner'):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError('Navigation needs ' + field)
    questions = value.get('open_questions')
    if not isinstance(questions, list) or any(not isinstance(item, str) or not item.strip() for item in questions):
        raise ValueError('Explicit open_questions list required')
    entries = value.get('sources')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Explicit navigation sources required')
    bound, seen = [], set()
    evidence = [{'path': source.relative_to(root).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest()}]
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('role') not in NAVIGATION_ROLES:
            raise ValueError('Unknown navigation source role')
        ref = entry.get('path')
        if not isinstance(ref, str) or not ref.strip() or Path(ref).is_absolute() or '..' in Path(ref).parts:
            raise ValueError('Navigation source must be workspace relative')
        target = (root / ref).resolve(strict=True); target.relative_to(root)
        data = target.read_bytes()
        if target.suffix.lower() == '.json':
            document = json.loads(data.decode('utf-8-sig'))
            if not isinstance(document, dict):
                raise ValueError('Navigation JSON source must be an object')
            nested = document.get('identity')
            nested_task = nested.get('task_id') if isinstance(nested, dict) else None
            if any(item is not None and item != task_id for item in (document.get('task_id'), nested_task)):
                raise ValueError('Navigation source task identity mismatch')
        key = (entry['role'], target)
        if key in seen:
            raise ValueError('Duplicate navigation source')
        seen.add(key)
        row = {'path': target.relative_to(root).as_posix(), 'sha256': hashlib.sha256(data).hexdigest()}
        evidence.append(row)
        bound.append({'role': entry['role'], **row})
    return {'sources': bound, 'open_questions': questions, 'recorded_next_action': value['next_action'],
            'next_action_owner': value['next_action_owner'], 'update_reason': value['update_reason'],
            'source_ref': source.relative_to(root).as_posix(), 'authority_verified': False}, evidence


def checkpoint(receipt, workspace, task_id, writer, output, *, navigation=None):
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
    if navigation is not None:
        record['navigation'], extra = navigation_context(navigation, root, task_id)
        record['evidence'].extend(extra)
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
    from task_resume import resume
    if resume(record, root)['status'] != 'CONTEXT_RESTORED':
        raise ValueError('Reconcile continuation sources before updating task card')
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
    if value.get('navigation'):
        nav = value['navigation']
        rows = ['来源导航（角色标签不证明批准或接受）：']
        rows.extend('- ' + row['role'] + ': [' + Path(row['path']).name + '](<' + (root / row['path']).as_posix() + '>)' for row in nav['sources'])
        rows.extend(['记录的下一步：' + nav['recorded_next_action'], '负责人：' + nav['next_action_owner'],
                     '未决事项：' + ('；'.join(nav['open_questions']) or '记录未列出；不等于科学审阅通过')])
        block = block.replace(stop, '\n'.join(rows) + '\n' + stop)
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
    parser.add_argument("--navigation", type=Path, help="explicit source navigation; grants no authority")
    args = parser.parse_args()
    checkpoint(args.receipt, args.workspace, args.task_id, args.writer, args.output, navigation=args.navigation)
    if args.task_card:
        link_task_card(args.task_card, args.output, args.workspace)

if __name__ == "__main__":
    main()
