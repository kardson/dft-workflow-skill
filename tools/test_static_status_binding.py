import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import preparation
import static_status_binding as binding
from test_environment_check import runtime_directory
from test_preparation import fixture, TOOLS


def setup(root):
    candidate, plan, approval, spec = fixture(root)
    record = preparation.audit(candidate, plan, approval, spec, root, TOOLS, sys.executable)
    path = root / 'prepared.json'
    path.write_text(json.dumps(record), encoding='utf-8')
    return candidate, path


class StaticStatusBindingTests(unittest.TestCase):
    def test_binding_preserves_candidate_and_is_not_execution_authority(self):
        with runtime_directory() as directory:
            root = Path(directory)
            candidate, prepared = setup(root)
            before = {path.name: path.read_bytes() for path in candidate.iterdir() if path.is_file()}
            result = binding.build(candidate, prepared, TOOLS, root / 'binding')
            self.assertFalse(result['execution_authorized'])
            self.assertFalse(result['remote_actions'])
            self.assertTrue(result['identity']['execution_id'])
            self.assertEqual(before, {path.name: path.read_bytes() for path in candidate.iterdir() if path.is_file()})
            self.assertFalse(set(result['upload_files']) & set(before))
            with self.assertRaises(FileExistsError):
                binding.build(candidate, prepared, TOOLS, root / 'binding')

    def test_changed_script_and_stale_identity_are_rejected(self):
        with runtime_directory() as directory:
            root = Path(directory)
            candidate, prepared = setup(root)
            manifest = candidate / 'execution_manifest.json'
            original = manifest.read_bytes()
            value = json.loads(original)
            value['task_id'] = 'changed-task'
            manifest.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):
                binding.build(candidate, prepared, TOOLS, root / 'stale')
            manifest.write_bytes(original)
            (candidate / 'run_static.sh').write_text('echo substituted\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                binding.build(candidate, prepared, TOOLS, root / 'changed')

    def test_runner_binds_actual_timing_and_refuses_reuse(self):
        with runtime_directory() as directory:
            root = Path(directory)
            station = root / 'station'
            station.mkdir()
            (station / 'case').mkdir()
            # A shell test double writes a timing file; no VASP is launched.
            (station / 'run_static.sh').write_text('synthetic runner placeholder', encoding='utf-8')
            identity = {'task_id': 'synthetic-task', 'unit_id': 'synthetic-unit', 'execution_id': 'synthetic-execution',
                        'remote_batch_dir': str(station.resolve()), 'case': 'case'}
            config = {'identity': identity, 'package_sha256': {'run_static.sh': preparation.digest(station / 'run_static.sh')}}
            (station / 'static_status_contract.json').write_text(json.dumps(config), encoding='utf-8')
            namespace = {'__name__': 'test_static_status_runner', '__file__': str(station / 'static_status_runner.py')}
            exec(compile(binding.RUNNER, 'static_status_runner.py', 'exec'), namespace)
            from unittest.mock import patch
            def simulate(argv, **kwargs):
                self.assertEqual(argv, ['bash', str(station / 'run_static.sh')])
                (station / 'case/run_timing.txt').write_text('exit_code=0\n', encoding='utf-8')
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(namespace['subprocess'], 'run', side_effect=simulate) as process:
                self.assertEqual(namespace['main'](), 0)
                timing = (station / 'case/run_timing.txt').read_text(encoding='utf-8')
                self.assertIn('execution_id=synthetic-execution\n', timing)
                with self.assertRaises(ValueError):
                    namespace['main']()
                self.assertEqual(process.call_count, 1)


if __name__ == '__main__':
    unittest.main()
