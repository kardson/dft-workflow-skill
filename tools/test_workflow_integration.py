"""Synthetic six-stage handoff, without a calculation or network connection."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

import analysis
import environment_check
import execution
import intake
import planning
import preparation
import recovery
import static_status_binding
from test_environment_check import runtime_directory
from test_intake import request
from test_preparation import fixture, TOOLS
from test_execution import fixture as snapshot_fixture, authority, verified
from test_recovery import fixture as recovery_fixture
from test_analysis import review
from progress_evidence import _load_raw_evidence_identity


class WorkflowIntegrationTests(unittest.TestCase):
    def test_prepared_identity_survives_terminal_recovery_and_partial_analysis(self):
        with runtime_directory() as directory:
            root = Path(directory)
            # An unverified workstation remains unverified; stage records do
            # not fabricate environment readiness or submission authority.
            self.assertEqual(environment_check.combine(None, None)['status'], 'UNVERIFIED')
            candidate, plan_path, approval_path, spec = fixture(root)
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            incoming = request()
            incoming['task_id'] = plan['task_id']
            incoming['evidence_gap']['new_calculation'] = 'YES'
            result = intake.validate(incoming, root)
            self.assertEqual(result['status'], 'READY_FOR_PLAN_DISCUSSION')
            (root / 'intake.json').write_text(json.dumps(result), encoding='utf-8')
            self.assertFalse(planning.validate(plan, root)['blockers'])
            checked = preparation.audit(candidate, plan_path, approval_path, spec, root, TOOLS, sys.executable)
            self.assertEqual(checked['status'], 'LOCAL_PACKAGE_CHECKED')
            self.assertEqual(checked['candidate_output_requirements_sha256'], analysis.digest(candidate / 'output_requirements.json'))
            prepared = root / 'preparation.json'
            prepared.write_text(json.dumps(checked), encoding='utf-8')

            static_status_binding.build(candidate, prepared, TOOLS, root / 'status-binding')
            manifest = root / 'status-binding/status_manifest.json'
            expected = _load_raw_evidence_identity(manifest)
            auth = authority(expected, execution.digest(manifest))
            snapshot, _ = snapshot_fixture('COMPLETED_PENDING_REVIEW')
            snapshot['identity'] = expected
            handoff = execution.evaluate(snapshot, verified(snapshot, expected), 'user', auth,
                                         manifest_sha=execution.digest(manifest))
            self.assertEqual(handoff['next_action'], 'HANDOFF_TO_RECOVERY_AND_ANALYSIS', handoff)
            simulated = root / 'simulated-transport'
            simulated.mkdir()
            _, payload = recovery_fixture(simulated)
            payload['manifest_identity'] = expected
            for member in payload['members']:
                name = member['name']
                member['source_path'] = recovery.source_path(expected, name)
                content = (candidate / name).read_bytes() if name in {'POSCAR', 'INCAR', 'KPOINTS'} else b'SYNTHETIC INCOMPLETE OUTPUT\n'
                if name == 'status.json':
                    content = json.dumps({**{key: expected[key] for key in ('task_id', 'unit_id', 'execution_id')},
                                          'status': 'COMPLETED_PENDING_REVIEW'}).encode()
                member.update(content_b64=base64.b64encode(content).decode(), sha256=hashlib.sha256(content).hexdigest())
                for stat in ('read_before', 'read_after'):
                    member[stat]['bytes'] = len(content)
            for name, value in {'authority': auth, 'handoff': handoff, 'payload': payload}.items():
                (root / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
            args = ['ingest', '--max-total-bytes', '1000000', '--transport-exit-code', '0']
            for name, value in {'manifest': manifest, 'handoff': root / 'handoff.json', 'authority': root / 'authority.json',
                                'payload': root / 'payload.json', 'tool-root': TOOLS, 'output-dir': root / 'returned'}.items():
                args += ['--' + name, str(value)]
            self.assertEqual(recovery.main(args), 0)
            facts = analysis.parse(root / 'returned/recovery_handoff.json', plan_path, 'static1',
                candidate / 'input_manifest.json', candidate / 'output_requirements.json', root, TOOLS,
                os.environ.get('DFT_SCIENCE_PYTHON', sys.executable), root / 'analysis', preparation=prepared)
            self.assertEqual(facts['observations']['parser_status'], 'PARTIAL')
            self.assertEqual(facts['backend_status'], 'SUCCEEDED', facts['warnings'])
            backend = json.loads(Path(facts['backend_ref']).read_text(encoding='utf-8'))
            self.assertIn(backend['result_module'], ('result_analysis', 'vasp_result_analysis'))
            self.assertEqual(set(backend['versions']), {'numpy', 'ase', 'pymatgen', 'matplotlib'})
            self.assertEqual(backend['summary']['status'], 'PARTIAL')
            self.assertTrue(backend['summary']['parse_error'])
            parsed = json.loads((root / 'analysis/parser/summary.json').read_text(encoding='utf-8'))
            self.assertEqual(parsed, backend['summary'])
            self.assertEqual(analysis.digest(Path(facts['backend_ref'])), facts['backend_sha256'])
            self.assertEqual(facts['scientific_acceptance'], 'NOT_GRANTED_BY_TOOL')
            failed = analysis.parse(root / 'returned/recovery_handoff.json', plan_path, 'static1',
                candidate / 'input_manifest.json', candidate / 'output_requirements.json', root, TOOLS,
                root / 'missing-analysis-python.exe', root / 'failed-analysis', preparation=prepared)
            self.assertEqual(failed['backend_status'], 'FAILED')
            self.assertEqual(failed['observations']['parser_status'], 'UNAVAILABLE')
            self.assertFalse((root / 'failed-analysis/backend.json').exists())
            review(root)
            review_path = root / 'review.json'
            reviewed = json.loads(review_path.read_text(encoding='utf-8'))
            reviewed['task_id'] = plan['task_id']
            review_path.write_text(json.dumps(reviewed), encoding='utf-8')
            delivery = analysis.render(root / 'analysis/facts.json', review_path, root, root / 'delivery', root / 'current_task.md')
            self.assertFalse(delivery['new_calculation_authorized'])
            self.assertIn('PARTIAL', (root / 'current_task.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
