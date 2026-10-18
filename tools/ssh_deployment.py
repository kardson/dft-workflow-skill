"""Authorized SSH deployment and separate supervisor launch. No automatic retry or monitoring."""
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from preparation import audit

REMOTE = """import base64,hashlib,json,sys
from pathlib import Path
value=json.load(sys.stdin)
root=Path(value['directory'])
if root.exists(): raise ValueError('destination already exists')
if root.parent.resolve()!=root.parent.absolute(): raise ValueError('linked destination parent')
root.mkdir(exist_ok=False)
for row in value['files']:
 path=root/row['name']
 raw=base64.b64decode(row['data'])
 if hashlib.sha256(raw).hexdigest()!=row['sha256']: raise ValueError('transport identity mismatch')
 with path.open('xb') as stream: stream.write(raw)
 if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']: raise ValueError('stored identity mismatch')
print('DEPLOYED:'+json.dumps({r['name']:r['sha256'] for r in value['files']}))
"""

def deploy(candidate, preparation, target, files, authority, runner=subprocess.run, *, audit_context=None):
    candidate=Path(candidate).resolve(strict=True)
    prepared=json.loads(Path(preparation).read_text(encoding='utf-8-sig'))
    digest=lambda raw:hashlib.sha256(raw).hexdigest()
    if prepared.get('schema')!='dft-preparation-handoff/v1' or prepared.get('status')!='LOCAL_PACKAGE_CHECKED' or prepared.get('blockers')!=[] or Path(prepared['candidate']).resolve()!=candidate:
        raise ValueError('Successful matching preparation required')
    for name,key in [('input_manifest.json','candidate_manifest_sha256'),('execution_manifest.json','candidate_execution_manifest_sha256'),('output_requirements.json','candidate_output_requirements_sha256')]:
        if digest((candidate/name).read_bytes())!=prepared.get(key):raise ValueError('Prepared package identity changed')
    if not isinstance(audit_context, dict) or set(audit_context)!={'plan_path','approval_path','approved_manifest','workspace','tool_root','python'}:
        raise ValueError('Explicit original preparation sources required for re-audit')
    for key,field in [('plan_path','plan_sha256'),('approval_path','approval_record_sha256'),('approved_manifest','approved_manifest_sha256')]:
        if digest(Path(audit_context[key]).read_bytes())!=prepared.get(field):
            raise ValueError('Original approval source changed')
    current=audit(candidate, **audit_context)
    if current.get('status')!='LOCAL_PACKAGE_CHECKED' or current.get('blockers')!=[]:
        raise ValueError('Current preparation audit blocks deployment')
    for field in ('task_id','stage_id','candidate_manifest_sha256','candidate_execution_manifest_sha256','candidate_output_requirements_sha256'):
        if current.get(field)!=prepared.get(field):raise ValueError('Current audit no longer matches preparation')
    manifest=json.loads((candidate/'execution_manifest.json').read_text())
    approved_host=manifest.get('host',{})
    if not isinstance(approved_host,dict) or target['host']!=approved_host.get('user','')+'@'+approved_host.get('address','') or target['port']!=approved_host.get('port'):
        raise ValueError('SSH endpoint differs from approved execution host')
    if target['python']!=manifest.get('environment',{}).get('python_bin'):
        raise ValueError('Workstation interpreter differs from approved environment')
    if manifest.get('remote_batch_dir')!=target['directory']:
        raise ValueError('Deployment directory differs from approved execution manifest')
    whitelist=json.loads((candidate/'remote_upload_whitelist.json').read_text())
    if whitelist.get('schema')!='vasp-practical-upload/v1' or set(files)!=set(whitelist.get('files',[])):
        raise ValueError('Complete approved fixed-cell upload whitelist required')
    checksums=json.loads((candidate/'runtime_checksums.json').read_text())
    for name, expected in checksums.items():
        if name not in files or Path(name).name!=name or digest((candidate/name).read_bytes())!=expected:
            raise ValueError('Runtime file differs from package checksums')
    gate=json.loads((candidate/'runtime_gate.json').read_text())
    if any(gate.get(key) is not False for key in ('user_authorized','sol_inputs_accepted','prepare','launch')):
        raise ValueError('Deploy only the original closed runtime gate; activate under separate authority')
    host=target['host'];port=target['port'];directory=target['directory'];python=target['python']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*',host) or type(port)is not int or not 1<=port<=65535:
        raise ValueError('Invalid SSH endpoint')
    for path in (directory,python):
        if not PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or any(ord(c)<32 for c in path):raise ValueError('Absolute safe workstation paths required')
    if directory=='/':raise ValueError('Root destination refused')
    if not isinstance(files,list) or not files or len(set(files))!=len(files):raise ValueError('Explicit unique upload list required')
    payload=[];identities={};total=0
    for name in files:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in {'.','..','POTCAR','WAVECAR','CHGCAR'}:raise ValueError('Unsafe or licensed upload member')
        path=candidate/name
        if path.is_symlink() or not path.is_file():raise ValueError('Regular upload member required')
        raw=path.read_bytes();total+=len(raw)
        if total>50000000:raise ValueError('Deployment byte budget exceeded')
        identities[name]=digest(raw);payload.append({'name':name,'data':base64.b64encode(raw).decode(),'sha256':digest(raw)})
    if authority.get('schema')!='dft-deployment-authority/v1' or authority.get('target')!=target or authority.get('files_sha256')!=identities or authority.get('preparation_sha256')!=digest(Path(preparation).read_bytes()) or not authority.get('user_confirmation',{}).get('reference') or not authority.get('user_confirmation',{}).get('quote'):
        raise ValueError('Exact deployment authority required')
    encoded=json.dumps({'directory':directory,'files':payload})
    command=shlex.join([python,'-c',REMOTE])
    child=runner(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','-p',str(port),host,command],input=encoded,capture_output=True,text=True,timeout=60,check=True)
    remote=json.loads(next(line[9:] for line in child.stdout.splitlines() if line.startswith('DEPLOYED:')))
    if remote!=identities:raise ValueError('Remote receipt differs from authorized file set')
    return {'schema':'dft-deployment-receipt/v1','target':target,'files_sha256':identities,'status':'DEPLOYED_NOT_STARTED','vasp_launched':False}


LAUNCH = """import hashlib,json,subprocess,sys
from pathlib import Path
value=json.load(sys.stdin)
root=Path(value['target']['directory'])
if root.resolve()!=root.absolute(): raise ValueError('linked runtime directory')
for name,expected in value['files'].items():
 if name=='runtime_gate.json': continue
 path=root/name
 if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise ValueError('deployed file changed')
gate_path=root/'runtime_gate.json'
if gate_path.is_symlink() or hashlib.sha256(gate_path.read_bytes()).hexdigest()!=value['gate_sha256']: raise ValueError('authorized gate differs')
sys.path.insert(0,str(root))
from practical_runtime import authorized
plan=authorized(root,value['action'])
if any(plan.get(k)!=value['identity'][k] for k in ('task_id','unit_id','execution_id')): raise ValueError('execution identity differs')
if plan.get('tmux_session')!=value['session']: raise ValueError('session identity differs')
marker=root/('.agent_'+value['action']+'_claim')
with marker.open('x') as stream: stream.write(json.dumps(value['identity']))
if value['action']=='prepare':
 from practical_runtime import prepare
 result=prepare(root)
 print('PREPARED:'+json.dumps(value['identity']))
else:
 from practical_runtime import bounded_validation
 policy=bounded_validation(plan)
 if policy:
  driver="import os,json,subprocess,time,pathlib,sys;v=json.loads(sys.argv[1]);env=dict(os.environ,DBUS_SESSION_BUS_ADDRESS='unix:path='+v['root']+'/.disabled_bus');socket=pathlib.Path(v['root'])/'.agent_tmux.sock';server=subprocess.Popen(['/usr/bin/tmux','-f','/dev/null','-D','-S',str(socket)],env=env);deadline=time.monotonic()+5\\nwhile not socket.exists() and time.monotonic()<deadline: time.sleep(.05)\\nif not socket.exists(): raise RuntimeError('private tmux server did not start')\\nsubprocess.run(['/usr/bin/tmux','-S',str(socket),'new-session','-e','DBUS_SESSION_BUS_ADDRESS='+os.environ.get('DBUS_SESSION_BUS_ADDRESS','unix:path=/run/user/'+str(os.getuid())+'/bus'),'-d','-s',v['session'],'-c',v['root'],'bash ./launch_supervisor.sh > .agent_supervisor.log 2>&1',';','set-option','-g','exit-empty','on'],env=env,check=True);sys.exit(server.wait())"
  argv=['systemd-run','--user','--collect','--service-type=exec','--unit='+policy['service_unit']]
  for prop in ('CPUQuota=10%','MemoryMax=2G','Nice=19','CPUAffinity='+str(policy['cpu']),'RuntimeMaxSec=600','TimeoutStopSec=30','KillMode=control-group'):
   argv+=['-p',prop]
  argv += ['/usr/bin/python3','-c',driver,json.dumps({'root':str(root),'session':value['session']})]
  subprocess.run(argv,check=True)
 else:
  subprocess.run(['tmux','new-session','-d','-s',value['session'],'-c',str(root),'bash ./launch_supervisor.sh'],check=True)
 print('LAUNCH_REQUESTED:'+json.dumps(value['identity']))
"""


def launch(deployment, authority, runner=subprocess.run, *, action="launch"):
    if action not in {"prepare","launch"}:raise ValueError("Unsupported runtime action")
    if deployment.get('schema')!='dft-deployment-receipt/v1' or deployment.get('status')!='DEPLOYED_NOT_STARTED':
        raise ValueError('Deployment receipt required')
    target=deployment['target']; identity=authority.get('identity',{})
    if authority.get('schema')!=('dft-'+action+'-authority/v1') or authority.get('target')!=target or authority.get('files_sha256')!=deployment['files_sha256']:
        raise ValueError('Launch authority must bind exact deployment')
    if not all(isinstance(identity.get(k),str) and identity[k].strip() for k in ('task_id','unit_id','execution_id')):
        raise ValueError('Explicit execution identity required')
    confirmation=authority.get('user_confirmation',{})
    if not confirmation.get('reference') or not confirmation.get('quote'):
        raise ValueError('Actual launch authorization reference required')
    gate=authority.get('gate_sha256','');session=authority.get('session','')
    if not re.fullmatch(r'[a-f0-9]{64}',gate) or not re.fullmatch(r'[A-Za-z0-9_-]+',session):
        raise ValueError('Explicit gate identity and safe session required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*',target['host']) or type(target['port'])is not int or not 1<=target['port']<=65535:
        raise ValueError('Invalid SSH endpoint')
    for path in (target['directory'],target['python']):
        if not PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or any(ord(c)<32 for c in path):raise ValueError('Unsafe target path')
    for name, digest_value in deployment['files_sha256'].items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in {'.','..'} or not re.fullmatch(r'[a-f0-9]{64}',digest_value):raise ValueError('Unsafe deployed member')
    payload={'action':action,'target':target,'files':deployment['files_sha256'],'gate_sha256':gate,'identity':identity,'session':session}
    command=shlex.join([target['python'],'-c',LAUNCH])
    result=runner(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','-p',str(target['port']),target['host'],command],input=json.dumps(payload),capture_output=True,text=True,timeout=30,check=True)
    prefix='PREPARED:' if action=='prepare' else 'LAUNCH_REQUESTED:'
    returned=json.loads(next(line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)))
    if returned!=identity:raise ValueError('Launch receipt identity mismatch')
    return {'schema':'dft-launch-request/v1','identity':identity,'status':'RUNTIME_PREPARED' if action=='prepare' else 'STARTUP_EVIDENCE_REQUIRED','next_action':'Use execution.py startup evidence check; do not claim normal progress from tmux creation'}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('deploy','prepare','launch'))
    parser.add_argument('--request',required=True,type=Path,help='Private JSON request with explicit authority and paths')
    parser.add_argument('--output',required=True,type=Path,help='New local receipt; reserved before any remote action')
    args=parser.parse_args()
    request=json.loads(args.request.read_text(encoding='utf-8-sig'))
    # Reserve first: an existing receipt cannot trigger a repeated remote action.
    with args.output.open('x',encoding='utf-8') as stream:
        try:
            result=deploy(**request) if args.mode=='deploy' else launch(**request,action=args.mode)
        except Exception as error:
            json.dump({'schema':'dft-remote-action-failure/v1','mode':args.mode,'status':'UNKNOWN_OR_FAILED','error_type':type(error).__name__,'next_action':'Inspect retained remote state and authorization; never retry automatically'},stream,indent=2)
            raise
        json.dump(result,stream,ensure_ascii=False,indent=2)

if __name__=='__main__':
    main()
