"""Explicit local WSL VASPKIT calls; no SSH, fallback or calculation launcher."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import uuid


PROGRAM = r'''
import json, os, pathlib, subprocess, sys, uuid
request = json.load(sys.stdin)
c, p = request['config'], request['payload']
binary = pathlib.Path(c['executable'])
base = pathlib.Path(c['work_root'])
base.mkdir(parents=True, exist_ok=True)
stage = base / uuid.uuid4().hex[:16]
stage.mkdir()
home = stage / 'home'; home.mkdir()
utilities = stage / 'u'; templates = utilities / 'INCAR_templates'
templates.mkdir(parents=True)
settings = (binary.resolve().parent.parent / 'how_to_set_environment_variables').read_text()
settings += '\nVASPKIT_UTILITIES_PATH = ' + str(utilities) + '\nUSER_DEFINED_INCAR = .TRUE.\nSET_INCAR_WRITE_MODE = OVERRIDE\nAUTO_PLOT = .FALSE.\n'
(home / '.vaspkit').write_text(settings)
env = dict(os.environ, HOME=str(home))
if p['operation'] == 'incar':
    (stage / 'POSCAR').write_text(p['poscar'])
    if p['profile'] != 'ST' or set(p['templates']) != {'Global', 'ST'}:
        raise ValueError('Only explicit Global/ST input templates are supported')
    for name, text in p['templates'].items():
        (templates / name).write_text(text)
    command = [str(binary), '-task', '101', '-inp', 'ST']
    output = stage / 'INCAR'
elif p['operation'] == 'potential':
    if p['axis'] not in ('x', 'y', 'z'): raise ValueError('Invalid axis')
    (stage / 'LOCPOT').write_text(p['locpot'])
    command = [str(binary), '-task', '426', '-dir', p['axis']]
    output = stage / 'PLANAR_AVERAGE.dat'
else:
    raise ValueError('Unsupported VASPKIT operation')
result = subprocess.run(command, cwd=stage, env=env, capture_output=True, text=True, timeout=90)
print(json.dumps({'returncode': result.returncode, 'stdout': result.stdout,
    'stderr': result.stderr, 'output': output.read_text() if output.is_file() else None,
    'stage': str(stage), 'command': command, 'transport': 'wsl',
    'execution_location': 'local', 'workstation_contacted': False,
    'vasp_launched': False, 'uid': os.getuid(), 'distribution': c['distribution']}))
'''


class WslVaspkitRunner:
    def __init__(self, config, receipt_dir=None):
        self.config = dict(config)
        if config.get('version') != '1.5.1':
            raise ValueError('This adapter is validated for VASPKIT 1.5.1 only')
        for field in ('distribution', 'user'):
            if not isinstance(config.get(field), str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', config[field]):
                raise ValueError('Explicit safe WSL distribution and user required')
        for field in ('executable', 'work_root'):
            path = PurePosixPath(config.get(field, ''))
            if not path.is_absolute() or '..' in path.parts or '\\' in str(path):
                raise ValueError('Explicit absolute Linux executable/work root required')
        home = PurePosixPath('/home') / config['user']
        if home not in PurePosixPath(config['work_root']).parents:
            raise ValueError('Use an isolated short work root under the selected Linux user home')
        if len(config['work_root']) > 48:
            raise ValueError('This adapter requires a short Linux work root (at most 48 characters)')
        self.receipts = []
        self.receipt_dir = Path(receipt_dir) if receipt_dir else None

    def invoke(self, payload):
        if payload.get('operation') not in {'incar', 'potential'}:
            raise ValueError('Unsupported local VASPKIT operation')
        argv = ['wsl.exe', '--distribution', self.config['distribution'], '--user', self.config['user'],
                '--exec', 'python3', '-c', PROGRAM]
        request = {'config': self.config, 'payload': payload}
        result = subprocess.run(argv, input=json.dumps(request), capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=120)
        if result.returncode:
            raise RuntimeError('Local WSL VASPKIT call failed: ' + result.stderr[-3000:])
        try:
            receipt = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise RuntimeError('Local WSL did not return one VASPKIT receipt') from error
        if (not isinstance(receipt, dict) or receipt.get('transport') != 'wsl'
                or receipt.get('execution_location') != 'local'
                or receipt.get('workstation_contacted') is not False):
            raise RuntimeError('Invalid local VASPKIT execution-location evidence')
        self.receipts.append(receipt)
        if self.receipt_dir:
            self.receipt_dir.mkdir(parents=True, exist_ok=True)
            (self.receipt_dir / (uuid.uuid4().hex + '.json')).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        if receipt.get('returncode') != 0 or not isinstance(receipt.get('output'), str) or not receipt['output'].strip():
            raise RuntimeError('VASPKIT produced no valid output; retained at ' + str(receipt.get('stage')))
        if not re.search(r'VASPKIT\s+(?:Standard\s+Edition|Version:)\s*1\.5\.1\b', receipt.get('stdout', '')):
            raise RuntimeError('Unexpected local VASPKIT version')
        return receipt['output']
