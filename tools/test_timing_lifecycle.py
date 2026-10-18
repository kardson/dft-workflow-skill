"""Offline quick-status lifecycle regressions; no workstation query."""
import ast,copy,json,os,subprocess,sys,unittest
from datetime import datetime,timezone
from unittest.mock import patch
from pathlib import Path
from test_execution import fixture
from test_environment_check import runtime_directory
sys.path.insert(0,os.environ.get('DFT_TOOL_ROOT',str(Path(__file__).resolve().parents[1]/'vasp_toolchain')))
from progress_evidence import _quick_status_canonical,quick_status,_load_quick_status_identity,build_quick_status_script


def running():
    snap,_=fixture()
    snap['observed_utc']=snap['remote_observed_utc']
    snap['sources']['status_json']['updated_utc']='2026-10-14T00:00:00+00:00'
    snap['sources']['run_timing']={'available':False,'raw_status':None,'identity_binding':{'state':'UNKNOWN'},'exit_codes':{},'error':None}
    snap['files']['run_timing.txt']={'reason':'missing','read_before':{'present':False},'read_after':None,'read_bytes':0,'read_error':None,'stable':False}
    snap['processes']=[{'state':'PRESENT','identity_match':'TASK_CONTEXT_MATCH','process_started_utc':'2026-10-14T00:00:01+00:00'}]
    stat={'present':True,'bytes':10,'mtime_utc':'2026-10-14T00:09:00+00:00'}
    snap['files']['OSZICAR']={'stable':True,'read_before':stat,'read_after':dict(stat),'read_error':None}
    return snap


class TimingLifecycleTests(unittest.TestCase):
    def test_generated_pid_context_checks_actual_command_and_start_time(self):
        from test_recovery import fixture as recovery_fixture
        with runtime_directory() as directory:
            root=Path(directory);recovery_fixture(root)
            script=build_quick_status_script(root/'manifest.json');tree=ast.parse(script)
            code='\n\n'.join(ast.get_source_segment(script,node) for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('probe_pid','mtime_utc'))
            proc=root/'mock-proc';pid=proc/'123';pid.mkdir(parents=True)
            (proc/'stat').write_text('btime 1000\n')
            (pid/'stat').write_text('123 (python3) S '+'0 '*18+'100\n')
            (pid/'cmdline').write_bytes(b'python3\0job_watch.py\0/synthetic/batch/watch.json\0')
            batch=root/'batch';batch.mkdir();case=batch/'case';case.mkdir()
            def mapped(value):return proc if str(value)=='/proc' else proc/str(value)[6:] if str(value).startswith('/proc/') else Path(value)
            original=Path.resolve
            def resolved(path,*args,**kwargs):return batch if path==pid/'cwd' else original(path,*args,**kwargs)
            namespace=dict(Path=mapped,BATCH=batch,CASE=case,datetime=datetime,timezone=timezone,os=os,subprocess=subprocess)
            exec(compile(code,'generated-probe','exec'),namespace)
            with patch.object(Path,'resolve',resolved),patch('os.sysconf',return_value=100,create=True),patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'123 S 00:01 python3','')):
                row=namespace['probe_pid'](123,['status.json.supervisor_pid'])
                self.assertEqual(row['identity_match'],'TASK_CONTEXT_MATCH')
                self.assertEqual(row['process_started_utc'],'1970-01-01T00:16:41Z')
                (pid/'cmdline').write_bytes(b'python3\0unrelated.py\0')
                self.assertEqual(namespace['probe_pid'](123,[])['identity_match'],'MISMATCH')

    def test_live_context_and_fresh_output_allow_pending_timing(self):
        snap=running()
        self.assertEqual(_quick_status_canonical(snap)[:2],('RUNNING','AGREED'))
        self.assertEqual(snap['sources']['run_timing']['lifecycle'],'TIMING_PENDING_UNTIL_EXIT')

    def test_old_state_or_pid_alone_is_not_running(self):
        for change in ('old_output','future_output','unstable_output','no_output','wrong_identity','dead_tmux','no_pid','pid_only','reused_pid','power_loss','naive_time','future_observation','future_status'):
            snap=running()
            if change=='old_output':snap['files']['OSZICAR']['read_before']['mtime_utc']=snap['files']['OSZICAR']['read_after']['mtime_utc']='2026-10-14T00:01:00+00:00'
            if change=='future_output':snap['files']['OSZICAR']['read_before']['mtime_utc']=snap['files']['OSZICAR']['read_after']['mtime_utc']='2999-01-01T00:00:00+00:00'
            if change=='unstable_output':snap['files']['OSZICAR']['stable']=False
            if change=='no_output':snap['progress']={}
            if change=='wrong_identity':snap['sources']['status_json']['identity_binding']['state']='MISMATCH'
            if change=='dead_tmux':snap['tmux']['pane_dead']=True
            if change=='no_pid':snap['processes']=[]
            if change=='pid_only':snap['processes'][0]['identity_match']='PID_ONLY_UNVERIFIED'
            if change=='reused_pid':snap['processes'][0]['process_started_utc']='2026-10-14T00:08:00+00:00'
            if change=='power_loss':snap['processes'][0]['state']='ABSENT'
            if change=='naive_time':snap['observed_utc']='2026-10-14T00:10:00'
            if change=='future_observation':snap['observed_utc']='2999-01-01T00:00:00+00:00'
            if change=='future_status':snap['sources']['status_json']['updated_utc']='2999-01-01T00:00:00+00:00'
            with self.subTest(change=change):self.assertEqual(_quick_status_canonical(snap)[0],'UNKNOWN')

    def test_present_or_contradictory_timing_cannot_be_pending(self):
        for change in ('wrong_identity','unreadable','unstable','completed','exit'):
            snap=running();snap['files']['run_timing.txt']={'stable':True,'read_before':{'present':True},'read_after':{'present':True}}
            snap['sources']['run_timing']={'available':True,'raw_status':'RUNNING','identity_binding':{'state':'MATCH'}}
            if change=='wrong_identity':snap['sources']['run_timing']['identity_binding']['state']='MISMATCH'
            if change=='unreadable':snap['files']['run_timing.txt']['read_error']='PermissionError'
            if change=='unstable':snap['files']['run_timing.txt']['stable']=False
            if change=='completed':snap['sources']['run_timing']['raw_status']='COMPLETED_PENDING_REVIEW'
            if change=='exit':snap['sources']['run_timing']['exit_codes']={'exit_code':0}
            with self.subTest(change=change):
                self.assertEqual(_quick_status_canonical(snap)[0],'UNKNOWN')
                self.assertNotEqual(snap['sources']['run_timing']['lifecycle'],'TIMING_PENDING_UNTIL_EXIT')

    def test_terminal_requires_final_timing_and_exit(self):
        snap,_=fixture('COMPLETED_PENDING_REVIEW')
        self.assertEqual(_quick_status_canonical(snap)[0],'COMPLETED_PENDING_REVIEW')
        snap['sources']['run_timing'].pop('exit_codes')
        self.assertEqual(_quick_status_canonical(snap)[0],'UNKNOWN')
        snap=running();snap['sources']['status_json']['raw_status']='COMPLETED_PENDING_REVIEW'
        self.assertEqual(_quick_status_canonical(snap)[0],'UNKNOWN')
        self.assertEqual(snap['sources']['run_timing']['lifecycle'],'TIMING_REQUIRED')

    def test_query_reasons_do_not_call_pending_timing_an_error(self):
        from test_recovery import fixture as recovery_fixture
        with runtime_directory() as directory:
            root=Path(directory);recovery_fixture(root);manifest=root/'manifest.json'
            doc=json.loads(manifest.read_text(encoding='utf-8'));doc['tmux_session']='synthetic-session';manifest.write_text(json.dumps(doc),encoding='utf-8')
            identity=_load_quick_status_identity(manifest);snap=running();snap['identity']=identity
            for role in ('status.json','OUTCAR','vasp.stdout','vasp.stderr'):
                snap['files'][role]={'stable':True,'read_before':{'present':True},'read_after':{'present':True}}
            def run(argv,**kwargs):return subprocess.CompletedProcess(argv,0,json.dumps(snap).encode(),b'')
            result=quick_status(manifest,runner=run)
            self.assertEqual(result['canonical_status'],'RUNNING')
            self.assertEqual(result['query_status'],'PARTIAL')
            self.assertFalse(any('timing' in reason.lower() for reason in result['reasons']),result['reasons'])


if __name__=='__main__':unittest.main()
