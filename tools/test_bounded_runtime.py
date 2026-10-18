"""Offline resource-boundary tests; no SSH, system changes, or solver launch."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.environ.get('DFT_TOOL_ROOT', str(Path(__file__).resolve().parents[1] / 'vasp_toolchain')))
from practical_runtime import bounded_validation, verify_bounded_context


def fixture():
    policy=dict(schema='vasp-bounded-validation/v1',task_id='synthetic',unit_id='synthetic',remote_batch_dir='/synthetic/test',cpu=2,cpu_quota_percent=10,memory_max_bytes=2147483648,wall_seconds=600,stop_grace_seconds=30,nice=19,service_unit='workflow-validation-synthetic',authorization_ref='synthetic authority')
    return dict(environment={'bounded_validation':policy},parallel=dict(mpi_ranks=1,kpar=1,ncore=1,omp_num_threads=1),**{k:policy[k] for k in ('task_id','unit_id','remote_batch_dir')})


class BoundedRuntimeTests(unittest.TestCase):
    def test_default_has_no_coexistence_permission(self):
        self.assertIsNone(bounded_validation({'environment':{}}))
        self.assertFalse(verify_bounded_context({'environment':{}}))

    def test_changed_scope_or_limits_are_rejected(self):
        plan=fixture()
        self.assertEqual(bounded_validation(plan),plan['environment']['bounded_validation'])
        for key,value in [('task_id','other'),('unit_id','other'),('remote_batch_dir','/other'),('cpu_quota_percent',100),('memory_max_bytes',9999999999),('wall_seconds',3600),('authorization_ref',''),('service_unit','ordinary-service')]:
            changed=copy.deepcopy(plan);changed['environment']['bounded_validation'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):bounded_validation(changed)
        plan['parallel']['mpi_ranks']=2
        with self.assertRaises(ValueError):bounded_validation(plan)

    def test_actual_context_requires_all_limits(self):
        def run_context(changed=None, affinity=None, properties=None):
            contents={'/proc/self/cgroup':'0::/synthetic/workflow-validation-synthetic.service', 'cpu.max':'10000 100000','memory.max':'2147483648'}
            contents.update(changed or {})
            def read(path,*args,**kwargs):return contents[path.as_posix()] if path.as_posix() in contents else contents[path.name]
            shown=properties or 'RuntimeMaxUSec=10min\nTimeoutStopUSec=30s\nKillMode=control-group\n'
            with patch.object(Path,'read_text',read),patch('practical_runtime.os.sched_getaffinity',return_value={2} if affinity is None else affinity,create=True),patch('practical_runtime.os.getpriority',return_value=19,create=True),patch('practical_runtime.os.PRIO_PROCESS',0,create=True),patch('practical_runtime.subprocess.run',return_value=subprocess.CompletedProcess([],0,shown,'')):
                return verify_bounded_context(fixture())
        self.assertTrue(run_context())
        for args in [dict(changed={'/proc/self/cgroup':'0::/other.service'}),dict(changed={'cpu.max':'max 100000'}),dict(changed={'memory.max':'max'}),dict(affinity={2,3}),dict(properties='RuntimeMaxUSec=infinity\nTimeoutStopUSec=30s\nKillMode=control-group')]:
            with self.subTest(args=args),self.assertRaises(ValueError):run_context(**args)


if __name__=='__main__':unittest.main()
