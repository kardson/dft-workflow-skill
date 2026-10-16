"""Build a closed, reusable fixed-cell runtime package from approved descriptors."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path, PurePosixPath
from runtime_identity import normalize_identity
from vasp_executor import (
    _load_requirements,
    is_performance_diagnostic,
    is_rmm_performance_diagnostic,
    performance_diagnostic_spec_errors,
    validate_inputs,
)

TOOLS=Path(__file__).resolve().parent
RUNTIME_FILES=('practical_runtime.py','vasp_executor.py','vasp_executor_core.py','vasp_contracts.py','vasp_profile_policy.py','progress_snapshot.py','vasp_execution_state.py','job_watch.py')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def output_requirements(source):
    source=copy.deepcopy(source)
    if source.get('schema')=='vasp-executor-output-requirements/v1':
        return source
    if source.get('kind')!='relaxation_capped_at_four_ionic_steps':
        raise ValueError('Legacy output contract unsupported; provide canonical output requirements')
    # Preserve the approved capped-run distinction between completion and
    # ionic convergence. The report itself can only be checked after writing.
    required=[x for x in source['required_files'] if x!='postcheck.json']
    nonempty=[x for x in source['nonempty_files'] if x!='postcheck.json']
    markers=[dict(file='OUTCAR',name='program_end',pattern=re.escape(source['normal_end_marker']),category='program'),
             dict(file='OUTCAR',name='electronic_ediff',pattern=re.escape(source['electronic_convergence_marker']),category='electronic')]
    if source.get('ionic_convergence_required_for_four_step_task_completion') is not False:
        raise ValueError('Capped-run ionic observation policy must be explicitly approved')
    for index,pattern in enumerate(source['ionic_convergence_markers_observation_only']):
        markers.append(dict(file='OUTCAR',name=f'ionic_observation_{index}',pattern=re.escape(pattern),category='ionic',required=False))
    identity={k.upper():v for k,v in source['identity'].items()}
    identity['VASP_VERSION']=identity.pop('VASP_VERSION')
    return dict(schema='vasp-executor-output-requirements/v1',kind='relaxation',
                required_files=required,nonempty_files=nonempty,optional_files=[],markers=markers,
                identity_fields=list(identity),expected_identity=identity,
                generated_receipt_files=['postcheck.json'],source_contract=source)

def describe(inputs,execution_source,requirements_source,environment):
    diagnostic=is_performance_diagnostic(inputs)
    diagnostic_errors=performance_diagnostic_spec_errors(inputs)
    if diagnostic_errors:
        raise ValueError(f'Invalid performance diagnostic contract: {diagnostic_errors}')
    verify_source_contract(inputs, execution_source, environment)
    identity=normalize_identity(execution_source,inputs)
    if isinstance(identity['host'],str):
        user,address=identity['host'].split('@',1)
        identity['host']=dict(user=user,address=address,port=identity['port'])
    if inputs['environment_id']!=environment['environment_id']:
        raise ValueError('No approved runtime environment profile for this task')
    for key in ('vasp_bin','mpi_launcher','tmux_bin','python_bin','paw_root'):
        value=environment[key]
        if not re.fullmatch(r'/[A-Za-z0-9_./+-]+',value) or '..' in PurePosixPath(value).parts:
            raise ValueError('Unsafe approved runtime path')
    if environment['mpi_args']!=['--map-by','core','--bind-to','core','--nooversubscribe','--report-bindings']:
        raise ValueError('Runtime MPI binding differs from the approved profile')
    if not diagnostic and (inputs['incar']['ISIF']!=2 or type(inputs['incar']['IBRION']) is not int or inputs['incar']['IBRION'] not in (1,2) or type(inputs['incar']['NSW']) is not int or inputs['incar']['NSW']<=0):
        raise ValueError('Generic runtime supports approved fixed-cell ionic relaxation only')
    parallel=inputs['parallel']
    if parallel['omp_num_threads']!=1 or environment['omp_num_threads']!=1:
        raise ValueError('Only the approved one-thread MPI layout is supported')
    if not isinstance(parallel['mpi_ranks'],int) or parallel['mpi_ranks']<=0:
        raise ValueError('MPI rank count must be approved explicitly')
    paw=inputs['paw_identity']
    if paw['paw_family']!=environment['paw_family']:
        raise ValueError('PAW archive family differs from the approved environment')
    roles=inputs['structure']['paw_order']
    if paw.get('ordered_roles')!=roles or paw.get('environment_id')!=inputs['environment_id']:
        raise ValueError('PAW mapping/environment differs from approved inputs')
    components=[]
    for role in roles:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',role) or role in ('.','..'):
            raise ValueError('Unsafe PAW role')
        record=paw[role]
        digest=record['component_sha256']
        if not re.fullmatch(r'[0-9a-f]{64}',digest):
            raise ValueError('PAW component identity missing')
        components.append(dict(name=role,relative_path=role+'/POTCAR',sha256=digest))
    requirements=output_requirements(requirements_source)
    if diagnostic and requirements.get('kind')!='static':
        raise ValueError('Fixed-geometry warm performance diagnostic requires static output semantics')
    identity_fields=requirements.setdefault('identity_fields',[])
    expected=requirements.setdefault('expected_identity',{})
    if expected.get('VASP_VERSION',environment['vasp_version'])!=environment['vasp_version']:
        raise ValueError('Output version contract conflicts with approved environment')
    expected['VASP_VERSION']=environment['vasp_version']
    if 'VASP_VERSION' not in identity_fields: identity_fields.append('VASP_VERSION')
    for category in (('program','diagnostic') if diagnostic else ('program','electronic')):
        if not any(x['category']==category and x.get('required',True) is True for x in requirements['markers']):
            raise ValueError('Output contract must retain the approved program and electronic/diagnostic gates')
    _,errors=_load_requirements(inputs,requirements)
    if errors: raise ValueError(f'Invalid approved output contract: {errors}')
    return dict(schema='vasp-practical-execution/v1',schema_version=1,route='vasp',delivery_template='vasp-practical-fixed-cell/v1',
                task_profile='fixed_geometry_warm_rmm_performance_diagnostic' if is_rmm_performance_diagnostic(inputs) else ('fixed_geometry_warm_performance_diagnostic' if diagnostic else 'fixed_cell_ionic_relaxation'),
                status='PREPARED_LOCAL_CLOSED',scientific_release='NOT_AUTHORIZED',
                execution_id=uuid.uuid4().hex,**identity,
                runtime_input_dir=identity['case_dir'],runtime_input_verified=False,
                parallel=copy.deepcopy(parallel),resource=copy.deepcopy(parallel),environment=copy.deepcopy(environment),
                restart=copy.deepcopy(inputs['restart']),
                paw=dict(family=paw['paw_family'],components=components,
                         combined_sha256=paw.get('combined_potcar_sha256')),
                output_requirements=requirements,
                actions=dict(upload=False,prepare=False,launch=False,automatic_retry=False,follow_on_task=False))

def verify_source_contract(inputs, source, environment):
    """Reject conflicting approved descriptors before invoking any backend."""
    diagnostic=is_performance_diagnostic(inputs)
    def check(record, expected, label, ignored=()):
        for key, value in expected.items():
            if key not in ignored and key in record and record[key] != value:
                raise ValueError(f'Execution source conflicts with approved inputs: {label}.{key}')
    structure=inputs['structure']; parallel=inputs['parallel']; paw=inputs['paw_identity']
    check(source.get('composition',{}),structure,'composition')
    check(source.get('resource',{}),parallel,'resource')
    check(source.get('parallel',{}),parallel,'parallel')
    lock=source.get('method_lock',{})
    check(lock,dict(environment_id=inputs['environment_id'],vasp_version=environment['vasp_version'],
                    paw_family=paw['paw_family'],ordered_roles=structure['paw_order'],
                    nions=structure['nions'],nelect=structure['nelect'],
                    kpoints=inputs['kpoints']['mesh'],**parallel),'method_lock',
          ignored={'kpar'} if diagnostic else ())
    if paw.get('combined_potcar_sha256'):
        check(lock,dict(combined_potcar_sha256=paw['combined_potcar_sha256']),'method_lock')
    aliases={'ENCUT_eV':'ENCUT','EDIFF_eV':'EDIFF','EDIFFG_eV_per_A':'EDIFFG','SIGMA_eV':'SIGMA'}
    incar={aliases.get(k,k):v for k,v in inputs['incar'].items()}
    incar.update(EFIELD=inputs['incar'].get('external_field',False),BEXT=inputs['incar'].get('external_field',False))
    check(lock.get('incar_summary',{}),incar,'incar_summary',
          ignored=({'NELM','NELMIN','LREAL','IBRION','NSW'} | ({'ALGO'} if is_rmm_performance_diagnostic(inputs) else set())) if diagnostic else ())
    restart=lock.get('restart',{})
    declared=inputs['restart']
    legacy_mode={'wavefunction_and_charge_scf':'WAVECAR+CHGCAR'}.get(declared['mode'],declared['mode'])
    check(restart,dict(mode=legacy_mode),'restart')
    if declared.get('source'):
        origin=declared['source']
        check(restart,dict(source_case=origin['case_path'],
                          wavecar_bytes=origin['files']['WAVECAR']['source_size_bytes'],
                          chgcar_bytes=origin['files']['CHGCAR']['source_size_bytes']),'restart')

def build(package,execution_spec,requirements_spec,environment_path=None):
    package=Path(package)
    inputs=json.loads((package/'input_manifest.json').read_text())
    source=json.loads(Path(execution_spec).read_text(encoding='utf-8-sig'))
    requirements=json.loads(Path(requirements_spec).read_text(encoding='utf-8-sig'))
    if environment_path is None:
        raise ValueError('An explicit workstation environment descriptor is required')
    environment=json.loads(Path(environment_path).read_text())
    plan=describe(inputs,source,requirements,environment)
    if not validate_inputs(package/'input_manifest.json',package)['passed']:
        raise ValueError('Actual prepared inputs failed the existing gate')
    if (package/'execution_manifest.json').exists():
        raise ValueError('Runtime manifest already exists')
    for name in RUNTIME_FILES:
        (package/name).write_bytes((TOOLS/name).read_bytes())
    (package/'output_requirements.json').write_text(json.dumps(plan['output_requirements'],indent=2))
    (package/'execution_source.json').write_bytes(Path(execution_spec).read_bytes())
    (package/'execution_manifest.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    shell='#!/usr/bin/env bash\nset -Eeuo pipefail\nROOT="$(cd -P -- "$(dirname -- "$0")" && pwd)"\nexec '+environment['python_bin']+' "$ROOT/practical_runtime.py" run --package "$ROOT"\n'
    (package/'run_task.sh').write_bytes(shell.encode())
    prepare=shell.replace('practical_runtime.py" run','practical_runtime.py" prepare')
    (package/'prepare_runtime.sh').write_bytes(prepare.encode())
    watch=dict(job=plan['task_id'],task_id=plan['task_id'],unit_id=plan['unit_id'],execution_id=plan['execution_id'],
               command=[environment['python_bin'],'./practical_runtime.py','run','--package',plan['remote_batch_dir']],
               cases=[dict(path=plan['case'],kind='static' if plan['task_profile'] in ('fixed_geometry_warm_performance_diagnostic','fixed_geometry_warm_rmm_performance_diagnostic') else 'relaxation',postcheck_receipt='postcheck.json',
                           task_id=plan['task_id'],unit_id=plan['unit_id'],execution_id=plan['execution_id'])],notify_argv=[])
    (package/'watch.json').write_text(json.dumps(watch,indent=2))
    supervisor=shell.replace('practical_runtime.py" run --package "$ROOT"','job_watch.py" "$ROOT/watch.json"')
    (package/'launch_supervisor.sh').write_bytes(supervisor.encode())
    immutable=['POSCAR','INCAR','KPOINTS','input_manifest.json','execution_manifest.json','output_requirements.json',*RUNTIME_FILES,'run_task.sh','prepare_runtime.sh','watch.json','launch_supervisor.sh']
    hashes={name:sha(package/name) for name in immutable}
    (package/'runtime_checksums.json').write_text(json.dumps(hashes,indent=2))
    gate=dict(schema='vasp-practical-runtime-gate/v2',task_id=plan['task_id'],unit_id=plan['unit_id'],
              execution_id=plan['execution_id'],plan_sha256=hashes['execution_manifest.json'],
              package_checksum_sha256=sha(package/'runtime_checksums.json'),
              user_authorized=False,sol_inputs_accepted=False,prepare=False,launch=False)
    (package/'runtime_gate.json').write_text(json.dumps(gate,indent=2))
    (package/'remote_upload_whitelist.json').write_text(json.dumps(dict(schema='vasp-practical-upload/v1',
        files=[*immutable,'runtime_checksums.json','runtime_gate.json'],excluded=['POTCAR','WAVECAR','CHGCAR','execution_source.json','geometry.extxyz','geometry_inspection.json']),indent=2))
    return dict(schema='vasp-practical-runtime-package/v1',passed=True,execution_id=plan['execution_id'],
                runner='run_task.sh',case=plan['case'],restart_mode=plan['restart_mode'],
                executable_template_ready=True,remote_deployment='NOT_PERFORMED',execution='NOT_AUTHORIZED')
