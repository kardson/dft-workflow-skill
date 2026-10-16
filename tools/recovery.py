"""Authorized terminal export and canonical raw-evidence/analysis handoff.

Generate a fixed read-only workstation script, or ingest its returned payload.
The agent uses its approved deployment transport; this module performs no SSH.
Raw bytes are preserved through the existing evidence importer. Partial failed
outputs remain partial and may be used for diagnosis, never filled or accepted.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from execution import TERMINAL, permitted
from preparation import digest

INPUTS = ("POSCAR", "INCAR", "KPOINTS")
OUTPUTS = ("OUTCAR", "OSZICAR", "CONTCAR", "vasprun.xml", "vasp.stdout", "vasp.stderr")
EXTRA_METADATA = ("postcheck.json", "execution_receipt.json", "paw_identity.json", "restart_copy_receipt.json")
ALLOWED = INPUTS + OUTPUTS + ("status.json", "run_timing.txt") + EXTRA_METADATA
REQUIRED = set(INPUTS + ("OUTCAR", "OSZICAR", "CONTCAR", "vasprun.xml", "status.json", "run_timing.txt"))

EXPORT = r'''import base64,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
IDENTITY = json.loads(__IDENTITY__)
MAX_BYTES = __MAX_BYTES__
def utc(): return datetime.now(timezone.utc).isoformat()
def stats(path):
    value=path.stat()
    return {'present':True,'bytes':value.st_size,'mtime_utc':datetime.fromtimestamp(value.st_mtime,timezone.utc).isoformat()}
batch=Path(IDENTITY['remote_batch_dir'])
case=Path(IDENTITY['remote_case_dir'])
inputs=Path(IDENTITY['remote_input_dir'])
status_path=batch/'.job_watch'/'status.json'
if status_path.is_symlink() or not status_path.is_file() or status_path.resolve()!=status_path.absolute(): raise ValueError('invalid status source')
with status_path.open('rb') as stream: status_bytes=stream.read(1024*1024+1)
if len(status_bytes)>1024*1024: raise ValueError('oversized status')
status=json.loads(status_bytes)
if any(status.get(k)!=IDENTITY[k] for k in ('task_id','unit_id','execution_id')): raise ValueError('wrong execution')
if status.get('status') not in ('COMPLETED_PENDING_REVIEW','COMPLETED_PENDING_SOL_REVIEW','FAILED_OR_INCOMPLETE','SUPERVISOR_ERROR'): raise ValueError('not terminal')
started=utc(); members=[]; missing=[]; total=0
for name in __ALLOWED__:
    path=status_path if name=='status.json' else (inputs/name if name in __INPUTS__ else case/name)
    if not path.exists(): missing.append(name); continue
    if path.is_symlink() or path.resolve()!=path.absolute() or not path.is_file(): raise ValueError('nonregular source '+name)
    total+=path.stat().st_size
    if total>MAX_BYTES: raise ValueError('declared transfer budget exceeded')
    before=stats(path)
    with path.open('rb') as stream: data=stream.read(before['bytes']+1)
    after=stats(path)
    if before!=after or len(data)!=before['bytes']: raise ValueError('source changed during read: '+name)
    members.append({'name':name,'source_path':str(path),'content_b64':base64.b64encode(data).decode('ascii'),
                    'sha256':hashlib.sha256(data).hexdigest(),'is_symlink':False,'read_before':before,'read_after':after})
with status_path.open('rb') as stream: final_status=stream.read(1024*1024+1)
if final_status!=status_bytes: raise ValueError('terminal status changed')
print(json.dumps({'schema':'dft-terminal-export/v1','manifest_identity':IDENTITY,'observed_start_utc':started,
                  'observed_end_utc':utc(),'members':members,'missing':missing,'terminal_state':status['status']},ensure_ascii=True))
'''

IDENTITY = "import json,sys;sys.path.insert(0,sys.argv[1]);from progress_evidence import _load_raw_evidence_identity;print(json.dumps(_load_raw_evidence_identity(sys.argv[2])))"
IMPORT = '''import base64,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from progress_evidence import ingest_export, PRIMARY_INPUTS, OUTPUT_ROLES
from vasp_operations import _frames_for_payload
payload=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
identity=payload['manifest_identity']
members=[{**row,'content':base64.b64decode(row['content_b64'],validate=True)} for row in payload['members']
         if row['name'] in PRIMARY_INPUTS+OUTPUT_ROLES+('status.json','run_timing.txt')]
frames=_frames_for_payload(identity,members,{row['name']:row for row in members},payload['observed_start_utc'],payload['observed_end_utc'])
bundle=ingest_export(frames,Path(sys.argv[3]),sys.argv[4],snapshot_name='terminal-raw',source_mode='authorized-terminal-export',
                    command_mode='external-authorized-read-only-transport',command_argv=['external-authorized-transport'],command_exit_code=0)
document=json.loads((bundle/'evidence.json').read_text(encoding='utf-8'))
print(json.dumps({'bundle':str(bundle),'collection_status':document['collection']['status']}))
'''


def source_path(identity, name):
    if name == "status.json":
        return identity["remote_batch_dir"] + "/.job_watch/status.json"
    return (identity["remote_input_dir"] if name in INPUTS else identity["remote_case_dir"]) + "/" + name


def export_script(identity, max_bytes):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Positive explicit transfer budget required")
    return EXPORT.replace("__IDENTITY__", repr(json.dumps(identity))).replace("__MAX_BYTES__", str(max_bytes)).replace("__ALLOWED__", repr(ALLOWED)).replace("__INPUTS__", repr(INPUTS))


def validate(payload, expected, max_bytes):
    if payload.get("schema") != "dft-terminal-export/v1" or type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Explicit export schema and positive byte budget required")
    identity = payload.get("manifest_identity", {})
    def timestamp(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Source observations need timezone-aware timestamps")
        return parsed
    start, end = timestamp(payload["observed_start_utc"]), timestamp(payload["observed_end_utc"])
    if start > end or end > datetime.now(timezone.utc):
        raise ValueError("Invalid or future export observations")
    for key in ("task_id", "unit_id", "execution_id", "host", "remote_batch_dir", "case", "remote_case_dir", "remote_input_dir", "runtime_input_source"):
        if identity.get(key) != expected.get(key) or (key == "execution_id" and not expected.get(key)):
            raise ValueError("Returned identity mismatch: " + key)
    rows, total = {}, 0
    for row in payload.get("members", []):
        name = row.get("name")
        if name not in ALLOWED or name in rows or row.get("is_symlink") is not False or row.get("source_path") != source_path(expected, name):
            raise ValueError("Unexpected, duplicate, linked or mislocated member")
        encoded = row.get("content_b64", "")
        if len(encoded) > (max_bytes * 4 // 3 + 8):
            raise ValueError("Encoded member exceeds budget")
        data = base64.b64decode(encoded, validate=True)
        total += len(data)
        if total > max_bytes or hashlib.sha256(data).hexdigest() != row.get("sha256"):
            raise ValueError("Returned content budget or digest mismatch")
        before, after = row.get("read_before"), row.get("read_after")
        if not isinstance(before, dict) or before != after or before.get("present") is not True or before.get("bytes") != len(data) or not before.get("mtime_utc"):
            raise ValueError("Returned source was incomplete or changed during read")
        if timestamp(before["mtime_utc"]) > end:
            raise ValueError("Source modification is later than export observation")
        rows[name] = data
    if "status.json" not in rows:
        raise ValueError("Returned terminal status required")
    status = json.loads(rows["status.json"])
    if any(status.get(key) != expected[key] for key in ("task_id", "unit_id", "execution_id")):
        raise ValueError("Returned status belongs to another execution")
    state = "COMPLETED_PENDING_REVIEW" if status.get("status") == "COMPLETED_PENDING_SOL_REVIEW" else status.get("status")
    if state not in TERMINAL:
        raise ValueError("Cannot ingest active execution as terminal recovery")
    missing = sorted(name for name in REQUIRED if not rows.get(name))
    return rows, state, missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["script", "ingest"])
    for name in ("manifest", "handoff", "authority", "tool-root", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--max-total-bytes", required=True, type=int)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--transport-exit-code", type=int)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    if args.output_dir.exists() or args.max_total_bytes <= 0:
        parser.error("Use a new output and positive explicit transfer budget")
    child = subprocess.run([str(args.python), "-B", "-c", IDENTITY, str(args.tool_root.resolve()), str(args.manifest.resolve())], capture_output=True, text=True, timeout=10, check=True)
    expected = json.loads(child.stdout)
    handoff, authority = (json.loads(path.read_text(encoding="utf-8-sig")) for path in (args.handoff, args.authority))
    if (handoff.get("schema") != "dft-execution-handoff/v1" or handoff.get("next_action") != "HANDOFF_TO_RECOVERY_AND_ANALYSIS"
            or handoff.get("state") not in TERMINAL or handoff.get("blockers")
            or any(handoff.get("identity", {}).get(key) != expected.get(key) for key in ("task_id", "unit_id", "execution_id"))
            or not all(permitted(authority, expected, action, handoff.get("trigger"), digest(args.manifest)) for action in ("RECOVER", "ANALYZE"))):
        raise PermissionError("Exact terminal handoff and existing recovery/analysis authority required")
    if args.mode == "script":
        script = export_script(expected, args.max_total_bytes)
        args.output_dir.mkdir(parents=True, exist_ok=False)
        (args.output_dir / "terminal_export.py").write_text(script, encoding="utf-8")
        print(json.dumps({"export_script_created": True, "remote_calls": False, "budget_bytes": args.max_total_bytes}))
        return 0
    if args.payload is None or args.transport_exit_code != 0:
        raise ValueError("Actual returned payload and successful external transport exit required")
    payload_limit = args.max_total_bytes * 4 // 3 + 1024 * 1024
    with args.payload.open("rb") as handle:
        payload_raw = handle.read(payload_limit + 1)
    if len(payload_raw) > payload_limit:
        raise ValueError("Payload exceeds declared budget")
    payload = json.loads(payload_raw)
    rows, state, missing = validate(payload, expected, args.max_total_bytes)
    if state != handoff["state"]:
        raise ValueError("Terminal state changed since observation; resolve the evidence before ingestion")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    retained = args.output_dir / "returned_payload.json"
    retained.write_bytes(payload_raw)
    child = subprocess.run([str(args.python), "-B", "-c", IMPORT, str(args.tool_root.resolve()), str(retained.resolve()),
                            str(args.output_dir.resolve()), str(args.manifest.resolve())], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=True)
    imported = json.loads(child.stdout)
    if imported["collection_status"] != "COMPLETE":
        raise ValueError("Existing evidence importer reported collection failures")
    view = args.output_dir / "analysis_source"
    view.mkdir()
    records = {}
    for name, data in rows.items():
        if name == "status.json":
            continue
        target = view / name
        with target.open("xb") as handle:
            handle.write(data)
        records[name] = {"path": str(target.resolve()), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "source_path": source_path(expected, name)}
    result = {"schema": "dft-recovery-handoff/v1", "identity": expected, "terminal_state": state,
              "status": "RECOVERED_PARTIAL" if missing else "RECOVERED_COMPLETE", "missing": missing,
              "analysis_scope": "PARTIAL_DIAGNOSTICS" if missing else "OUTPUTS_READY_FOR_REVIEW",
              "raw_bundle": imported["bundle"], "analysis_source": str(view.resolve()), "files": records,
              "export_observed_utc": payload.get("observed_end_utc"), "recovery_performed": True,
              "transport": "external authorized transport; successful exit reported by agent", "remote_calls_by_this_tool": False,
              "scientific_acceptance": "NOT_EVALUATED", "handoff_sha256": digest(args.handoff)}
    manifest_doc = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    contract = manifest_doc.get("output_requirements")
    if isinstance(contract, str) and Path(contract).name == contract and (args.manifest.parent / contract).is_file():
        contract = json.loads((args.manifest.parent / contract).read_text(encoding="utf-8-sig"))
    result["completeness_scope"] = "core files; declared output contract checked separately"
    result["declared_output_contract_available"] = isinstance(contract, dict)
    result["declared_outputs_missing"] = sorted(name for name in contract.get("required_files", [])
                                                if name not in rows or (name in contract.get("nonempty_files", []) and not rows[name])) if isinstance(contract, dict) else None
    result["additional_metadata"] = sorted(set(rows) & set(EXTRA_METADATA))
    (args.output_dir / "recovery_handoff.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "README.md").write_text("# 原始输出回收交付\n\n状态：" + result["status"] + "\n\n缺失：" + json.dumps(missing) + "\n\n原始证据与解析用副本已保存，仍需分析收敛及科学用途。\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "missing": missing, "scientific_acceptance": "NOT_EVALUATED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
