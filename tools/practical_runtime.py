"""Reusable fixed-cell runner using the existing executor gates; no SSH or retry.

All entrypoints require a separate, package-bound explicit authorization gate.
Preparing or running this file is not part of local package generation.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

def stamp(): return datetime.now(timezone.utc).isoformat()

def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,indent=2),encoding='utf-8')
    temporary.replace(path)

def hash_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()

def regular(path):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size<=0:
        raise ValueError(f'Required regular nonempty file unavailable: {path.name}')
    return path

def authorized(package,action):
    package=Path(package)
    plan=load(package/'execution_manifest.json')
    gate=load(package/'runtime_gate.json')
    if plan.get('schema')!='vasp-practical-execution/v1' or gate.get('schema')!='vasp-practical-runtime-gate/v2':
        raise ValueError('Unsupported runtime/gate schema')
    if not all(gate.get(key) is True for key in ('user_authorized','sol_inputs_accepted',action)):
        raise PermissionError('Runtime permission is closed; no action performed')
    for key in ('task_id','unit_id','execution_id'):
        if gate.get(key)!=plan.get(key): raise ValueError('Runtime gate identity mismatch')
    if gate.get('plan_sha256')!=hash_file(package/'execution_manifest.json'):
        raise ValueError('Runtime gate plan identity mismatch')
    if gate.get('package_checksum_sha256')!=hash_file(package/'runtime_checksums.json'):
        raise ValueError('Runtime gate package identity mismatch')
    for name,expected in load(package/'runtime_checksums.json').items():
        if Path(name).name!=name or name in ('.','..'):
            raise ValueError('Unsafe immutable package member')
        if hash_file(regular(package/name))!=expected:
            raise ValueError(f'Immutable runtime package differs: {name}')
    return plan

def location(package,plan):
    package=Path(package)
    if package.is_symlink() or str(package.resolve())!=plan['remote_batch_dir']:
        raise ValueError('Runtime must use the exact approved batch directory')
    case=package/plan['case']
    if case.is_symlink() or str(case)!=plan['case_dir'] or case.parent!=package:
        raise ValueError('Runtime case directory differs from approved identity')
    return case

def copy_restart(source,target,expected_size):
    source=regular(source)
    before=source.stat()
    if before.st_size!=expected_size or target.exists():
        raise ValueError('Restart size differs or target already exists')
    digest=hashlib.sha256()
    with source.open('rb') as reader,target.open('xb') as writer:
        for block in iter(lambda:reader.read(1024*1024),b''):
            digest.update(block);writer.write(block)
    after=source.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
        raise ValueError('Restart source changed during independent copy')
    actual=target.stat()
    if actual.st_size!=expected_size or actual.st_nlink!=1 or hash_file(target)!=digest.hexdigest():
        raise ValueError('Restart copy failed integrity/independence verification')
    return dict(source_path=str(source),bytes=actual.st_size,source_sha256=digest.hexdigest(),
                target_sha256=digest.hexdigest(),target_nlink=actual.st_nlink,target_mtime_ns=actual.st_mtime_ns)

def materialize_case(package,case,plan):
    """Local file operation seam for synthetic tests; callers own authorization."""
    package=Path(package);case=Path(case)
    if case.parent!=package or case.exists() or case.is_symlink():
        raise ValueError('Runtime case must be an absent direct child of the batch')
    stage=package/('.runtime_stage_'+uuid.uuid4().hex)
    stage.mkdir(mode=0o777)
    # Failures preserve this stage for diagnosis, never overwrite a case.
    from vasp_executor import validate_inputs
    for name in ('POSCAR','INCAR','KPOINTS'):
        shutil.copyfile(regular(package/name),stage/name)
    if not validate_inputs(package/'input_manifest.json',stage)['passed']:
        raise ValueError('Staged actual inputs do not match approved specification')
    paw_root=Path(plan['environment']['paw_root'])
    components=[]
    with (stage/'POTCAR').open('xb') as writer:
        for component in plan['paw']['components']:
            relative=Path(component['relative_path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe PAW component path')
            source=regular(paw_root/relative)
            if source.resolve().is_relative_to(paw_root.resolve()) is False:
                raise ValueError('PAW component resolves outside approved archive')
            digest=hashlib.sha256()
            with source.open('rb') as reader:
                for block in iter(lambda:reader.read(1024*1024),b''):
                    digest.update(block);writer.write(block)
            if digest.hexdigest()!=component['sha256']:
                raise ValueError('PAW component differs from the exact approved archive')
            components.append(dict(name=component['name'],sha256=digest.hexdigest()))
    combined=hash_file(stage/'POTCAR')
    if plan['paw'].get('combined_sha256') and combined!=plan['paw']['combined_sha256']:
        raise ValueError('Combined POTCAR differs from the approved identity')
    restart=plan['restart'];copies={}
    if restart.get('mode')=='wavefunction_and_charge_scf':
        for name in ('WAVECAR','CHGCAR'):
            record=restart['source']['files'][name]
            source=Path(record['source_path'])
            if source.parent!=Path(restart['source']['case_path']):
                raise ValueError('Restart path differs from the approved source case')
            copies[name]=copy_restart(source,stage/name,record['source_size_bytes'])
    elif restart.get('fresh') is not True:
        raise ValueError('Undeclared restart mode; no fresh fallback')
    receipt=dict(schema='vasp-practical-case-preparation/v1',execution_id=plan['execution_id'],
                 task_id=plan['task_id'],unit_id=plan['unit_id'],created_utc=stamp(),
                 components=components,combined_potcar_sha256=combined,restart_copies=copies,
                 input_sha256={name:hash_file(stage/name) for name in ('POSCAR','INCAR','KPOINTS')})
    save(stage/'runtime_case_receipt.json',receipt)
    if case.exists() or case.is_symlink(): raise ValueError('Case appeared during staging')
    # Atomically reserve the absent name. rename() can replace an empty
    # directory on POSIX; mkdir() cannot overwrite a concurrently created case.
    case.mkdir()
    for file in stage.iterdir():
        if file.name!='runtime_case_receipt.json': file.rename(case/file.name)
    (stage/'runtime_case_receipt.json').rename(case/'runtime_case_receipt.json')
    stage.rmdir()
    return receipt

def prepare(package):
    package=Path(package).absolute()
    plan=authorized(package,'prepare');case=location(package,plan)
    return materialize_case(package,case,plan)

def verify_case_receipt(case,plan):
    case=Path(case)
    receipt=load(regular(case/'runtime_case_receipt.json'))
    for key in ('task_id','unit_id','execution_id'):
        if receipt.get(key)!=plan.get(key): raise ValueError('Prepared case identity differs')
    expected=[dict(name=x['name'],sha256=x['sha256']) for x in plan['paw']['components']]
    if receipt.get('components')!=expected: raise ValueError('PAW component mapping differs')
    if hash_file(regular(case/'POTCAR'))!=receipt['combined_potcar_sha256']:
        raise ValueError('Runtime POTCAR differs from verified assembly')
    if plan['paw'].get('combined_sha256') and receipt['combined_potcar_sha256']!=plan['paw']['combined_sha256']:
        raise ValueError('Runtime POTCAR differs from the approved combined identity')
    for name,digest in receipt['input_sha256'].items():
        if name not in ('POSCAR','INCAR','KPOINTS') or hash_file(regular(case/name))!=digest:
            raise ValueError('Case input differs from staged input')
    if plan['restart'].get('mode')=='wavefunction_and_charge_scf':
        for name in ('WAVECAR','CHGCAR'):
            copy=receipt['restart_copies'][name];source=plan['restart']['source']['files'][name]
            info=regular(case/name).stat()
            if copy['source_path']!=source['source_path'] or info.st_size!=source['source_size_bytes'] or info.st_nlink!=1:
                raise ValueError('Runtime restart identity/size/independence differs')
            if info.st_mtime_ns!=copy['target_mtime_ns'] or copy['source_sha256']!=copy['target_sha256']:
                raise ValueError('Runtime restart changed after verified copy')
    elif receipt['restart_copies']:
        raise ValueError('Fresh case contains a restart copy declaration')
    return receipt

def runtime_environment(plan):
    profile=plan['environment'];env=dict(os.environ)
    env['OMP_NUM_THREADS']='1'
    env['LD_LIBRARY_PATH']=':'.join(profile['ld_library_path'])
    for key in ('vasp_bin','mpi_launcher','tmux_bin'):
        regular(profile[key])
        if not os.access(profile[key],os.X_OK): raise ValueError('Approved runtime executable unavailable')
    version=subprocess.run([profile['mpi_launcher'],'--version'],capture_output=True,text=True,env=env,check=True,timeout=15)
    if version.stdout.splitlines()[0]!=profile['mpi_version']:
        raise ValueError('MPI runtime differs from approved version')
    libraries=subprocess.run(['ldd',profile['vasp_bin']],capture_output=True,text=True,env=env,check=True,timeout=15)
    if 'not found' in libraries.stdout+libraries.stderr: raise ValueError('Unresolved VASP runtime library')
    if not os.environ.get('TMUX') or not os.environ.get('TMUX_PANE'):
        raise ValueError('Runner must use the approved dedicated tmux session')
    session=subprocess.run([profile['tmux_bin'],'display-message','-p','-t',os.environ['TMUX_PANE'],'#{session_name}'],capture_output=True,text=True,check=True,timeout=15)
    if session.stdout.strip()!=plan['tmux_session']: raise ValueError('Wrong tmux session')
    affinity=os.sched_getaffinity(0)
    physical=set()
    for cpu in affinity:
        topology=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        physical.add((int((topology/'physical_package_id').read_text()),int((topology/'core_id').read_text())))
    if len(physical)<plan['parallel']['mpi_ranks']: raise ValueError('Insufficient allowed physical cores')
    processes=subprocess.run(['ps','-eo','comm='],capture_output=True,text=True,check=True,timeout=15)
    if any(x.strip() in ('vasp_std','vasp_gam','vasp_ncl','mpirun','prterun','orterun') for x in processes.stdout.splitlines()):
        raise ValueError('Another VASP/MPI process is active')
    return env

def launch_once(argv,case,plan,env,process_runner=None,on_locked=None):
    """Create the single-run lock after executor preflight, then use exact cwd."""
    case=Path(case)
    (case/'.run_once').mkdir(mode=0o700)
    if on_locked is not None: on_locked()
    started=time.time()
    result=None
    try:
        with (case/'vasp.stdout').open('x') as stdout,(case/'vasp.stderr').open('x') as stderr:
            result=(process_runner or subprocess.run)(argv,cwd=str(case),env=env,stdout=stdout,stderr=stderr,check=False)
        return result
    finally:
        # Raw failures remain failures; there is no retry or recovery here.
        with (case/'run_timing.txt').open('x') as timing:
            timing.write(f'unit_id={plan["unit_id"]}\ntask_id={plan["task_id"]}\nexecution_id={plan["execution_id"]}\n')
            timing.write(f'case_dir={case}\nstart_epoch={started}\nend_epoch={time.time()}\nexit_code={result.returncode if result is not None else 255}\n')

def run(package):
    package=Path(package).absolute()
    plan=authorized(package,'launch');case=location(package,plan)
    if os.name!='posix': raise ValueError('This runner requires the approved Linux workstation')
    verify_case_receipt(case,plan)
    env=runtime_environment(plan)
    supervisor=load(package/'.job_watch/status.json')
    if supervisor.get('status')!='RUNNING' or supervisor.get('runner_pid')!=os.getpid():
        raise ValueError('Runner must be owned by the existing single-run supervisor')
    if any(supervisor.get(key)!=plan[key] for key in ('task_id','unit_id','execution_id')):
        raise ValueError('Supervisor identity differs from approved runtime plan')
    return execute_case(package,case,plan,env)

def execute_case(package,case,plan,env,process_runner=None):
    """Existing executor integration seam; injectable only for offline tests."""
    package=Path(package);case=Path(case)
    from vasp_executor import execute_once
    profile=plan['environment']
    argv=[profile['mpi_launcher'],'--np',str(plan['parallel']['mpi_ranks']),*profile['mpi_args'],profile['vasp_bin']]
    status=dict(schema='vasp-execution-state/v1',task_id=plan['task_id'],unit_id=plan['unit_id'],
                execution_id=plan['execution_id'],case=plan['case'],runner_pid=os.getpid(),status='STARTING',observed_utc=stamp())
    claimed=False
    # The existing executor owns actual input/restart preflight and postcheck.
    def launcher(command,**kwargs):
        nonlocal claimed
        if kwargs.get('cwd')!=str(case.resolve()): raise ValueError('Executor launcher cwd differs')
        def on_locked():
            nonlocal claimed
            claimed=True
            status.update(status='RUNNING',observed_utc=stamp())
            save(case/'execution_receipt.json',dict(status,mpi_launcher=command,scientific_acceptance='NOT_AUTHORIZED'))
        return launch_once(command,case,plan,env,process_runner=process_runner,on_locked=on_locked)
    result=execute_once(package/'input_manifest.json',package,case,argv,package/'output_requirements.json',launcher)
    if not claimed:
        # A rejected preflight or failed concurrent lock must never modify an
        # existing case, status or another invocation's execution receipt.
        return result
    save(case/'postcheck.json',result)
    status.update(status='COMPLETED_PENDING_REVIEW' if result['passed'] else 'FAILED_OR_INCOMPLETE',observed_utc=stamp())
    save(case/'execution_receipt.json',dict(status,mpi_launcher=argv,scientific_acceptance='NOT_AUTHORIZED',program_exit=result['program_exit']))
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run'])
    parser.add_argument('--package',required=True)
    args=parser.parse_args()
    try:
        result=prepare(args.package) if args.action=='prepare' else run(args.package)
        print(json.dumps(result,indent=2))
        return 0 if args.action=='prepare' or result['passed'] else 2
    except Exception as error:
        print(json.dumps(dict(status='STOPPED',error_type=type(error).__name__,error=str(error),automatic_retry=False)))
        return 2

if __name__=='__main__': raise SystemExit(main())
