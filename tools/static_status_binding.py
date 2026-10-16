"""Prepare a closed static supervisor sidecar; no deployment or execution."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import uuid

from preparation import digest

RUNNER = '''"""Run the unchanged approved static runner and bind its timing evidence."""
import hashlib,json,os,subprocess
from pathlib import Path

def main():
    root=Path(__file__).resolve().parent
    config=json.loads((root/'static_status_contract.json').read_text(encoding='utf-8'))
    identity=config['identity']
    if root != Path(identity['remote_batch_dir']).resolve():
        raise ValueError('Static supervisor deployed outside approved batch')
    for name,expected in config['package_sha256'].items():
        path=root/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Approved static package changed: '+name)
    case=root/identity['case']
    timing=case/'run_timing.txt'
    if case.is_symlink() or timing.exists() or timing.is_symlink():
        raise ValueError('Refuse linked case or prior execution timing')
    code=subprocess.run(['bash',str(root/'run_static.sh')],cwd=root,check=False).returncode
    if timing.is_file() and not timing.is_symlink():
        raw=timing.read_text(encoding='utf-8')
        keys=[line.split('=',1)[0] for line in raw.splitlines() if '=' in line]
        if any(key in keys for key in ('task_id','unit_id','execution_id')):
            raise ValueError('Timing already contains an execution identity')
        with timing.open('a',encoding='utf-8',newline='\\n') as stream:
            if raw and not raw.endswith('\\n'):stream.write('\\n')
            for key in ('task_id','unit_id','execution_id'):
                stream.write(key+'='+identity[key]+'\\n')
    return code

if __name__=='__main__':
    raise SystemExit(main())
'''


def build(candidate, preparation, tool_root, output):
    candidate, output = Path(candidate).resolve(), Path(output).resolve()
    prepared = json.loads(Path(preparation).read_text(encoding='utf-8-sig'))
    original = json.loads((candidate / 'execution_manifest.json').read_text(encoding='utf-8'))
    if (prepared.get('status') != 'LOCAL_PACKAGE_CHECKED' or prepared.get('blockers')
            or Path(prepared.get('candidate', '')).resolve() != candidate
            or prepared.get('candidate_manifest_sha256') != digest(candidate / 'input_manifest.json')
            or prepared.get('candidate_execution_manifest_sha256') != digest(candidate / 'execution_manifest.json')
            or original.get('package', {}).get('runner') != 'run_static.sh'):
        raise ValueError('Matching checked static package required')
    check = subprocess.run([sys.executable, '-B', str(Path(tool_root) / 'static_delivery_check.py'),
                            '--candidate-dir', str(candidate)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=30)
    if check.returncode != 0 or json.loads(check.stdout).get('passed') is not True:
        raise ValueError('Actual static package no longer passes its existing checker')
    identity = {key: original.get(key) for key in ('task_id', 'unit_id', 'host', 'remote_batch_dir', 'case', 'tmux_session')}
    for key in ('task_id', 'unit_id', 'remote_batch_dir', 'case', 'tmux_session'):
        if not isinstance(identity[key], str) or not identity[key] or '\n' in identity[key] or '\r' in identity[key]:
            raise ValueError('Explicit safe static execution identity required')
    batch = PurePosixPath(identity['remote_batch_dir'])
    if not batch.is_absolute() or '..' in batch.parts or PurePosixPath(identity['case']).name != identity['case'] or identity['case'] in {'.', '..'}:
        raise ValueError('Unsafe remote batch/case')
    identity['execution_id'] = uuid.uuid4().hex
    runtime = json.loads((candidate / 'runtime_dependencies.json').read_text(encoding='utf-8'))
    python = runtime['python_bin']
    if not PurePosixPath(python).is_absolute():
        raise ValueError('Approved workstation Python must be absolute')
    uploads = json.loads((candidate / 'remote_upload_whitelist.json').read_text(encoding='utf-8'))['files']
    immutable = {}
    for name in uploads:
        path = candidate / name
        if Path(name).name != name or path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe approved upload member')
        # The established scientific approval gate stays mutable and closed.
        if name != 'sol_review_gate.json':
            immutable[name] = digest(path)
    watch = {**identity, 'command': [python, './static_status_runner.py'],
             'cases': [{'path': identity['case'], 'kind': 'static'}]}
    bound = {**original, 'execution_id': identity['execution_id'],
             'runtime_input_dir': str(batch / identity['case']), 'runtime_input_verified': False,
             'static_status_preparation_sha256': digest(preparation)}
    payload = {'static_status_runner.py': RUNNER.encode(),
               'static_status_contract.json': (json.dumps({'schema': 'dft-static-status-contract/v1', 'identity': identity,
                    'package_sha256': immutable}, indent=2) + '\n').encode(),
               'watch.json': (json.dumps(watch, indent=2) + '\n').encode(),
               'launch_supervisor.sh': ('#!/usr/bin/env bash\nset -Eeuo pipefail\nROOT="$(cd -P -- "$(dirname -- "$0")" && pwd)"\nexec ' + shlex.quote(python) + ' "$ROOT/job_watch.py" "$ROOT/watch.json"\n').encode()}
    for name in ('job_watch.py', 'vasp_execution_state.py'):
        path = Path(tool_root) / name
        if path.is_symlink():
            raise ValueError('Runtime module must be a regular file')
        payload[name] = path.read_bytes()
    if set(payload) & set(uploads):
        raise ValueError('Supervisor sidecar overlaps approved static package')
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in payload.items():
        (output / name).write_bytes(raw)
    (output / 'status_manifest.json').write_text(json.dumps(bound, indent=2) + '\n', encoding='utf-8')
    receipt = {'schema': 'dft-static-status-binding/v1', 'identity': identity,
               'candidate': str(candidate), 'preparation_ref': str(Path(preparation).resolve()),
               'original_execution_manifest_sha256': digest(candidate / 'execution_manifest.json'),
               'status_manifest_sha256': digest(output / 'status_manifest.json'),
               'upload_files': sorted(payload), 'sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in payload.items()},
               'execution_authorized': False, 'remote_actions': False,
               'deployment': 'Merge only listed sidecar members into the same approved batch; retain original static files and gate. Query/recover using status_manifest.json.'}
    (output / 'binding.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate', 'preparation', 'tool-root', 'output-dir'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.candidate, args.preparation, args.tool_root, args.output_dir)))


if __name__ == '__main__':
    main()
