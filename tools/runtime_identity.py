"""Validate approved input and execution identities without loading workstation profiles."""
import re
from pathlib import PurePosixPath

def normalize_identity(execution, inputs):
    for key in ('task_id','unit_id'):
        if execution.get(key) != inputs.get(key):
            raise ValueError(f'Approved execution/input {key} differs')
    if 'remote_target' in execution:
        remote=execution['remote_target']
        batch=remote['batch_dir'];case=remote['case_id'];case_dir=remote['case_dir']
        session=remote['tmux_session']
        host=remote['host'];port=remote['port']
    else:
        batch=execution['remote_batch_dir'];case=execution['case'];case_dir=execution['case_dir']
        session=execution['tmux_session']
        host=execution['host'];port=host['port']
        if execution.get('environment_id') != inputs.get('environment_id'):
            raise ValueError('Execution/input environment differs')
        if execution.get('runtime_input_dir') != case_dir:
            raise ValueError('Runner runtime inputs must be the exact case directory')
    if not PurePosixPath(batch).is_absolute() or '..' in PurePosixPath(batch).parts:
        raise ValueError('Unsafe remote batch directory')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',case) or case in ('.','..'):
        raise ValueError('Unsafe case basename')
    if case_dir != str(PurePosixPath(batch)/case):
        raise ValueError('Case directory differs from batch/case identity')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',session):
        raise ValueError('Unsafe tmux identity')
    return dict(task_id=inputs['task_id'],unit_id=inputs['unit_id'],host=host,port=port,
                remote_batch_dir=batch,case=case,case_dir=case_dir,tmux_session=session,
                environment_id=inputs['environment_id'],restart_mode=inputs['restart'].get('mode','fresh'))
