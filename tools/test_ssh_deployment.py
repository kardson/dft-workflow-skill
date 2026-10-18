import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from ssh_deployment import REMOTE, deploy
from test_environment_check import runtime_directory

class DeploymentTests(unittest.TestCase):
    def execute(self, payload):
        with patch.object(sys,'stdin',io.StringIO(json.dumps(payload))),contextlib.redirect_stdout(io.StringIO()) as out:
            exec(compile(REMOTE,'remote-deploy','exec'),{})
        return out.getvalue()
    def test_exclusive_remote_transfer_and_readback(self):
        with runtime_directory() as temp:
            root=Path(temp)/'new'
            payload={'directory':str(root),'files':[{'name':'INCAR','data':base64.b64encode(b'synthetic').decode(),'sha256':hashlib.sha256(b'synthetic').hexdigest()}]}
            result=self.execute(payload)
            self.assertIn('DEPLOYED:',result)
            self.assertEqual((root/'INCAR').read_bytes(),b'synthetic')
            with self.assertRaises(ValueError):self.execute(payload)
            self.assertEqual((root/'INCAR').read_bytes(),b'synthetic')
    def test_transport_corruption_does_not_write_member(self):
        with runtime_directory() as temp:
            root=Path(temp)/'new'
            payload={'directory':str(root),'files':[{'name':'INCAR','data':base64.b64encode(b'changed').decode(),'sha256':hashlib.sha256(b'original').hexdigest()}]}
            with self.assertRaises(ValueError):self.execute(payload)
            self.assertFalse((root/'INCAR').exists())

    def test_deployment_without_original_sources_never_calls_ssh(self):
        with runtime_directory() as temp:
            root=Path(temp);candidate=root/'candidate';candidate.mkdir()
            prepared={'schema':'dft-preparation-handoff/v1','status':'LOCAL_PACKAGE_CHECKED','blockers':[],'candidate':str(candidate)}
            for name,key in [('input_manifest.json','candidate_manifest_sha256'),('execution_manifest.json','candidate_execution_manifest_sha256'),('output_requirements.json','candidate_output_requirements_sha256')]:
                (candidate/name).write_bytes(b'{}');prepared[key]=hashlib.sha256(b'{}').hexdigest()
            receipt=root/'preparation.json';receipt.write_text(json.dumps(prepared))
            with patch('ssh_deployment.subprocess.run') as remote:
                with self.assertRaisesRegex(ValueError,'original preparation sources'):
                    deploy(candidate,receipt,{},[],{},runner=remote)
                remote.assert_not_called()

    def test_actual_fixed_package_audit_authority_and_transfer(self):
        import subprocess
        from test_preparation import fixed_fixture, TOOLS
        import preparation
        with runtime_directory() as temp:
            root=Path(temp); candidate,plan,approval,spec=fixed_fixture(root)
            context={'plan_path':plan,'approval_path':approval,'approved_manifest':spec,'workspace':root,'tool_root':TOOLS,'python':sys.executable}
            prepared=preparation.audit(candidate,**context)
            self.assertEqual(prepared['status'],'LOCAL_PACKAGE_CHECKED',prepared)
            receipt=root/'prepared.json';receipt.write_text(json.dumps(prepared))
            manifest=json.loads((candidate/'execution_manifest.json').read_text())
            target={'host':manifest['host']['user']+'@'+manifest['host']['address'],'port':manifest['host']['port'],'directory':manifest['remote_batch_dir'],'python':manifest['environment']['python_bin']}
            files=json.loads((candidate/'remote_upload_whitelist.json').read_text())['files']
            hashes={name:hashlib.sha256((candidate/name).read_bytes()).hexdigest() for name in files}
            authority={'schema':'dft-deployment-authority/v1','target':target,'files_sha256':hashes,'preparation_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest(),'user_confirmation':{'reference':'synthetic-user','quote':'synthetic deployment only'}}
            calls=[]
            def transfer(argv, **kwargs):
                calls.append(argv)
                payload=json.loads(kwargs['input'])
                self.assertEqual({row['name'] for row in payload['files']},set(files))
                self.assertEqual(payload['directory'],target['directory'])
                return subprocess.CompletedProcess(argv,0,'DEPLOYED:'+json.dumps(hashes),'')
            result=deploy(candidate,receipt,target,files,authority,runner=transfer,audit_context=context)
            self.assertEqual(result['status'],'DEPLOYED_NOT_STARTED')
            self.assertEqual(len(calls),1)
            authority['target']={**target,'host':'other.invalid'}
            with self.assertRaisesRegex(ValueError,'authority'):
                deploy(candidate,receipt,target,files,authority,runner=transfer,audit_context=context)
            self.assertEqual(len(calls),1)
            (candidate/'run_task.sh').write_text('altered runtime')
            checks=json.loads((candidate/'runtime_checksums.json').read_text());checks['run_task.sh']=preparation.digest(candidate/'run_task.sh')
            (candidate/'runtime_checksums.json').write_text(json.dumps(checks))
            with self.assertRaises(ValueError):
                deploy(candidate,receipt,target,files,authority,runner=transfer,audit_context=context)
            self.assertEqual(len(calls),1)

    def test_launch_is_separate_and_requires_startup_evidence(self):
        from ssh_deployment import launch
        import subprocess
        target={'host':'example.invalid','port':22,'directory':'/approved/batch','python':'/usr/bin/python3'}
        deployment={'schema':'dft-deployment-receipt/v1','status':'DEPLOYED_NOT_STARTED','target':target,'files_sha256':{'launch_supervisor.sh':'a'*64}}
        identity={'task_id':'t','unit_id':'u','execution_id':'e'}
        authority={'schema':'dft-launch-authority/v1','target':target,'files_sha256':deployment['files_sha256'],'identity':identity,'gate_sha256':'b'*64,'session':'synthetic','user_confirmation':{'reference':'synthetic','quote':'synthetic launch'}}
        def run(argv,**kwargs): return subprocess.CompletedProcess(argv,0,'LAUNCH_REQUESTED:'+json.dumps(identity),'')
        self.assertEqual(launch(deployment,authority,runner=run)['status'],'STARTUP_EVIDENCE_REQUIRED')
        authority['gate_sha256']='unknown'
        with self.assertRaises(ValueError): launch(deployment,authority,runner=run)

    def test_remote_launch_checks_actual_gate_and_refuses_repeat(self):
        from ssh_deployment import LAUNCH
        from test_preparation import fixed_fixture
        import preparation
        import subprocess
        with runtime_directory() as temp:
            root=Path(temp);candidate,*_=fixed_fixture(root)
            manifest=json.loads((candidate/'execution_manifest.json').read_text())
            files=json.loads((candidate/'remote_upload_whitelist.json').read_text())['files']
            hashes={name:preparation.digest(candidate/name) for name in files}
            gate_path=candidate/'runtime_gate.json'
            gate=json.loads(gate_path.read_text())
            identity={key:manifest[key] for key in ('task_id','unit_id','execution_id')}
            payload={'action':'launch','target':{'directory':str(candidate)},'files':hashes,'gate_sha256':preparation.digest(gate_path),'identity':identity,'session':manifest['tmux_session']}
            def execute():
                with patch.object(sys,'stdin',io.StringIO(json.dumps(payload))),contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(LAUNCH,'remote-launch','exec'),{})
            with patch('subprocess.run') as process:
                with self.assertRaises(PermissionError):execute()
                process.assert_not_called()
                self.assertFalse((candidate/'.agent_launch_claim').exists())
                gate.update(user_authorized=True,sol_inputs_accepted=True,launch=True)
                gate_path.write_text(json.dumps(gate));payload['gate_sha256']=preparation.digest(gate_path)
                execute()
                process.assert_called_once()
                self.assertEqual(process.call_args.args[0][:3],['tmux','new-session','-d'])
                with self.assertRaises(FileExistsError):execute()
                process.assert_called_once()
