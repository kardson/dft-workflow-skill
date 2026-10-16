import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import analysis
from test_environment_check import runtime_directory
from test_planning import fixture as plan_fixture

TOOLS = Path(os.environ.get("DFT_TOOL_ROOT", str(Path(__file__).resolve().parent)))
SCIENCE_PYTHON = os.environ.get("DFT_SCIENCE_PYTHON")


def synthetic_xml(nelm=60):
    structure = '<crystal><varray name="basis"><v>3 0 0</v><v>0 3 0</v><v>0 0 3</v></varray></crystal><varray name="positions"><v>0 0 0</v></varray>'
    energies = '<energy><i name="e_fr_energy">-1.0</i><i name="e_0_energy">-0.9</i><i name="e_wo_entrp">-0.9</i></energy>'
    return ('<?xml version="1.0"?><modeling><!-- SYNTHETIC PARSER TEST; NOT A VASP CALCULATION -->'
            '<generator><i name="version" type="string">6.5.1</i></generator>'
            '<incar><i name="ALGO" type="string">Normal</i></incar>'
            '<parameters><i name="NELM" type="int">' + str(nelm) + '</i><i name="NSW" type="int">0</i><i name="IBRION" type="int">-1</i></parameters>'
            '<atominfo><array name="atoms"><set><rc><c>Si</c><c>1</c></rc></set></array>'
            '<array name="atomtypes"><set><rc><c>1</c><c>Si</c><c>28</c><c>4</c><c>SYNTHETIC Si</c></rc></set></array></atominfo>'
            '<structure name="initialpos">' + structure + '</structure><calculation><scstep>' + energies + '</scstep>' + energies +
            '<structure>' + structure + '</structure><varray name="forces"><v>0.01 0 0</v></varray></calculation>'
            '<structure name="finalpos">' + structure + '</structure></modeling>')


def fixture(root, broken=False, nelm=60):
    plan = plan_fixture(root)
    (root / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    source = root / 'returned'
    source.mkdir()
    data = {'POSCAR': 'Synthetic Si\n1\n3 0 0\n0 3 0\n0 0 3\nSi\n1\nSelective Dynamics\nDirect\n0 0 0 T T T\n',
            'INCAR': 'NSW=0\nENCUT=300\n', 'KPOINTS': 'Synthetic\n0\nGamma\n1 1 1\n0 0 0\n',
            'OUTCAR': 'SYNTHETIC OUTPUT\nGeneral timing and accounting informations\naborting loop because EDIFF is reached\n',
            'OSZICAR': 'DAV: 1 -1.0 0.0 0.0 1 0.0\n 1 F= -1.0 E0= -0.9 d E = 0.0\n',
            'CONTCAR': 'SYNTHETIC ONLY\n', 'vasprun.xml': '<modeling>' if broken else synthetic_xml(nelm), 'run_timing.txt': 'exit_code=0\n'}
    files = {}
    for name, content in data.items():
        path = source / name
        path.write_text(content, encoding='utf-8')
        files[name] = {'path': str(path.resolve()), 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    recovery = {'schema': 'dft-recovery-handoff/v1', 'recovery_performed': True, 'identity': {'task_id': plan['task_id'], 'unit_id': 'synthetic-unit', 'execution_id': 'synthetic-execution'},
                'terminal_state': 'COMPLETED_PENDING_REVIEW', 'analysis_source': str(source.resolve()), 'files': files, 'missing': [], 'export_observed_utc': '2026-10-14T00:00:00+00:00'}
    (root / 'recovery.json').write_text(json.dumps(recovery), encoding='utf-8')
    spec = {'schema_version': 1, 'route': 'vasp', 'task_id': plan['task_id'], 'unit_id': 'synthetic-unit',
            'incar': {'NSW': 0}, 'restart': {'fresh': True, 'ISTART': 0, 'ICHARG': 2}}
    (root / 'spec.json').write_text(json.dumps(spec), encoding='utf-8')
    (root / 'requirements.json').write_text(json.dumps({'schema': 'vasp-executor-output-requirements/v1', 'kind': 'static', 'required_files': ['OUTCAR'], 'nonempty_files': ['OUTCAR'], 'markers': []}), encoding='utf-8')
    (root / 'preparation.json').write_text(json.dumps({'status': 'LOCAL_PACKAGE_CHECKED', 'task_id': plan['task_id'],
        'stage_id': 'static1', 'plan_sha256': analysis.digest(root / 'plan.json'),
        'candidate_manifest_sha256': analysis.digest(root / 'spec.json'),
        'candidate_output_requirements_sha256': analysis.digest(root / 'requirements.json')}), encoding='utf-8')
    return recovery


def fake_backend(argv, **kwargs):
    output = Path(argv[5])
    summary = {'status': 'PARTIAL', 'steps': []}
    (output / 'backend.json').write_text(json.dumps({'summary': summary, 'postcheck': {'passed': False}}), encoding='utf-8')
    return subprocess.CompletedProcess(argv, 0, 'DFT_ANALYSIS_JSON:{}', '')


def run_parse(root, python=sys.executable, runner=fake_backend):
    return analysis.parse(root / 'recovery.json', root / 'plan.json', 'static1', root / 'spec.json', root / 'requirements.json', root, TOOLS, python, root / 'analysis', runner=runner, preparation=root / 'preparation.json')


def review(root):
    facts = root / 'analysis/facts.json'
    value = {'schema': 'dft-result-review/v1', 'task_id': 'synthetic-plan', 'facts_sha256': analysis.digest(facts), 'reviewed_by': 'synthetic-reviewer',
             'answer': 'Evidence is insufficient for a material-stability conclusion.', 'confidence_and_basis': 'Only synthetic records; no scientific acceptance.',
             'comparability': 'No cross-structure comparison was performed.', 'observations': [{'text': 'Parser completeness is recorded.', 'evidence_refs': [str(facts) + '#/observations/parser_status']}],
             'interpretations': [{'text': 'This does not establish a physical conclusion.', 'evidence_refs': [str(facts) + '#/warnings']}],
             'limits': 'Synthetic parser test only.', 'next_action': 'No new calculation.', 'next_action_reason': 'This is a tool test.', 'next_action_owner': 'Research owner'}
    (root / 'review.json').write_text(json.dumps(value), encoding='utf-8')
    return value


class AnalysisTests(unittest.TestCase):
    def test_requirements_must_match_preparation_before_backend_runs(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root)
            original = root / 'requirements.json'
            required = json.loads(original.read_text(encoding='utf-8'))
            required['required_files'].append('vasprun.xml')
            required['nonempty_files'].append('vasprun.xml')
            original.write_text(json.dumps(required), encoding='utf-8')
            prepared = {'status': 'LOCAL_PACKAGE_CHECKED', 'task_id': 'synthetic-plan', 'stage_id': 'static1',
                        'plan_sha256': analysis.digest(root / 'plan.json'),
                        'candidate_manifest_sha256': analysis.digest(root / 'spec.json'),
                        'candidate_output_requirements_sha256': analysis.digest(original)}
            path = root / 'preparation.json'
            path.write_text(json.dumps(prepared), encoding='utf-8')
            def forbidden(*args, **kwargs):
                self.fail('Unbound requirements must be rejected before backend launch')
            with self.assertRaisesRegex(ValueError, 'handoff is required'):
                analysis.parse(root / 'recovery.json', root / 'plan.json', 'static1', root / 'spec.json', original,
                               root, TOOLS, sys.executable, root / 'analysis', runner=forbidden)
            weak = root / 'weak.json'
            weaker = json.loads(json.dumps(required))
            weaker['required_files'].remove('vasprun.xml')
            weaker['nonempty_files'].remove('vasprun.xml')
            weak.write_text(json.dumps(weaker), encoding='utf-8')
            for requirements in (weak, original):
                if requirements == original:
                    prepared.pop('candidate_output_requirements_sha256')
                    path.write_text(json.dumps(prepared), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Output requirements'):
                    analysis.parse(root / 'recovery.json', root / 'plan.json', 'static1', root / 'spec.json', requirements,
                                   root, TOOLS, sys.executable, root / 'analysis', runner=forbidden, preparation=path)

    def test_delivery_rechecks_actual_recovered_outputs(self):
        for action in ('replace', 'delete', 'add'):
            with self.subTest(action=action), runtime_directory() as directory:
                root = Path(directory)
                fixture(root); run_parse(root); review(root)
                if action == 'replace':
                    (root / 'returned/OUTCAR').write_text('changed original output', encoding='utf-8')
                elif action == 'delete':
                    (root / 'returned/OUTCAR').unlink()
                else:
                    (root / 'returned/vasp.stdout').write_text('unrecorded output', encoding='utf-8')
                with self.assertRaises(ValueError):
                    analysis.render(root / 'analysis/facts.json', root / 'review.json', root, root / 'delivery', root / 'card.md')
                self.assertFalse((root / 'delivery').exists())
                self.assertFalse((root / 'card.md').exists())

    def test_backend_failure_is_not_partial_output_and_cannot_be_delivered(self):
        for failure in ('missing-python', 'timeout', 'exit', 'no-record', 'bad-record'):
            with self.subTest(failure=failure), runtime_directory() as directory:
                root = Path(directory)
                fixture(root)
                def fail(argv, **kwargs):
                    if failure == 'timeout':
                        raise subprocess.TimeoutExpired(argv, 120)
                    if failure == 'bad-record':
                        (Path(argv[5]) / 'backend.json').write_text('{}', encoding='utf-8')
                    return subprocess.CompletedProcess(argv, 2 if failure == 'exit' else 0, 'DFT_ANALYSIS_JSON:{}', '')
                python = root / 'nonexistent-python.exe' if failure == 'missing-python' else sys.executable
                facts = run_parse(root, python, subprocess.run if failure == 'missing-python' else fail)
                self.assertEqual(facts['backend_status'], 'FAILED')
                self.assertEqual(facts['observations']['parser_status'], 'UNAVAILABLE')
                self.assertEqual(facts['interpretation'], 'BLOCKED_BACKEND_FAILURE')
                self.assertIsNone(facts['backend_ref'])
                review(root)
                with self.assertRaisesRegex(ValueError, 'backend evidence'):
                    analysis.render(root / 'analysis/facts.json', root / 'review.json', root, root / 'delivery')

    def test_cli_returns_failure_when_analysis_interpreter_is_missing(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root)
            argv = ['analyze', '--stage-id', 'static1']
            for name, path in {'recovery': root / 'recovery.json', 'plan': root / 'plan.json',
                               'approved-manifest': root / 'spec.json', 'requirements': root / 'requirements.json',
                               'tool-root': TOOLS, 'workspace': root, 'output-dir': root / 'analysis',
                               'preparation': root / 'preparation.json',
                               'python': root / 'missing-python.exe'}.items():
                argv += ['--' + name, str(path)]
            self.assertEqual(analysis.main(argv), 1)
            self.assertEqual(json.loads((root / 'analysis/facts.json').read_text(encoding='utf-8'))['backend_status'], 'FAILED')

    def test_changed_output_contract_or_processing_method_blocks_delivery(self):
        for filename in ('requirements.json', 'analysis/analysis_driver.py'):
            with self.subTest(filename=filename), runtime_directory() as directory:
                root = Path(directory)
                fixture(root)
                run_parse(root)
                review(root)
                path = root / filename
                path.write_text(path.read_text(encoding='utf-8') + '\nchanged\n', encoding='utf-8')
                with self.assertRaises(ValueError):
                    analysis.render(root / 'analysis/facts.json', root / 'review.json', root, root / 'delivery')

    def test_changed_raw_source_and_sensitive_file_are_rejected(self):
        with runtime_directory() as directory:
            root = Path(directory)
            record = fixture(root)
            (root / 'returned/INCAR').write_text('changed')
            with self.assertRaises(ValueError):
                analysis.sources(record, root)
            (root / 'returned/POTCAR').write_text('synthetic forbidden sentinel')
            with self.assertRaises(ValueError):
                analysis.sources(record, root)

    def test_partial_facts_are_explicit_and_unaccepted(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root, True)
            facts = run_parse(root)
            self.assertEqual(facts['observations']['parser_status'], 'PARTIAL')
            self.assertIsNone(facts['observations']['electronic_converged'])
            self.assertEqual(facts['observations']['ionic_converged'], 'NOT_APPLICABLE_TO_STATIC_TASK')
            self.assertEqual(facts['scientific_acceptance'], 'NOT_GRANTED_BY_TOOL')

    def test_report_updates_only_matching_card_slot_and_preserves_history(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root); run_parse(root); review(root)
            card = root / 'current.md'
            original = '\ufeffPREVIOUS HISTORY\r\n<!-- AGENT_TASK_CARD_BEGIN:synthetic-plan -->old<!-- AGENT_TASK_CARD_END:synthetic-plan -->\r\nEND HISTORY'
            card.write_bytes(original.encode('utf-8'))
            result = analysis.render(root / 'analysis/facts.json', root / 'review.json', root, root / 'delivery', card)
            saved = card.read_bytes().decode('utf-8')
            self.assertTrue(saved.startswith('\ufeffPREVIOUS HISTORY\r\n'))
            self.assertTrue(saved.endswith('\r\nEND HISTORY'))
            self.assertIn('2026-10-14', saved)
            self.assertFalse(result['new_calculation_authorized'])

    def test_stale_review_and_nonexistent_evidence_field_are_rejected(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root); run_parse(root); value = review(root)
            value['observations'][0]['evidence_refs'][0] += '/missing'
            (root / 'review.json').write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                analysis.render(root / 'analysis/facts.json', root / 'review.json', root, root / 'delivery')
            value = review(root)
            with (root / 'analysis/facts.json').open('a') as stream:
                stream.write('\n')
            with self.assertRaises(ValueError):
                analysis.render(root / 'analysis/facts.json', root / 'review.json', root, root / 'delivery')

    def test_nonfinite_values_and_unmarked_history_are_not_silently_accepted(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root)
            def nonfinite(argv, **kwargs):
                output = Path(argv[5])
                value = {'summary': {'status': 'PARTIAL', 'steps': [{'energy_zero_eV': float('nan')}]}, 'postcheck': {'passed': False}}
                (output / 'backend.json').write_text(json.dumps(value), encoding='utf-8')
                return subprocess.CompletedProcess(argv, 0, 'DFT_ANALYSIS_JSON:{}', '')
            facts = run_parse(root, runner=nonfinite)
            self.assertIsNone(facts['observations']['steps'][0]['energy_zero_eV'])
            self.assertTrue(any('非有限' in warning for warning in facts['warnings']))
            card = root / 'history.md'
            card.write_text('Original historical notes', encoding='utf-8')
            with self.assertRaises(ValueError):
                analysis.update_card(card, 'synthetic-plan', 'new card')
            self.assertEqual(card.read_text(encoding='utf-8'), 'Original historical notes')

    def test_preparation_binding_must_match_plan_and_numerical_manifest(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root)
            prepared = {'status': 'LOCAL_PACKAGE_CHECKED', 'task_id': 'synthetic-plan', 'stage_id': 'static1',
                        'plan_sha256': analysis.digest(root / 'plan.json'), 'candidate_manifest_sha256': 'wrong'}
            path = root / 'preparation.json'
            path.write_text(json.dumps(prepared))
            with self.assertRaises(ValueError):
                analysis.parse(root / 'recovery.json', root / 'plan.json', 'static1', root / 'spec.json', root / 'requirements.json',
                               root, TOOLS, sys.executable, root / 'analysis', runner=fake_backend, preparation=path)

    @unittest.skipUnless(SCIENCE_PYTHON, 'Set DFT_SCIENCE_PYTHON for the actual installed science parser')
    def test_real_parser_complete_partial_and_nonconverged(self):
        for broken, nelm in ((False, 60), (True, 60), (False, 1)):
            with self.subTest(broken=broken, nelm=nelm), runtime_directory() as directory:
                root = Path(directory)
                fixture(root, broken, nelm)
                facts = run_parse(root, SCIENCE_PYTHON, subprocess.run)
                self.assertEqual(facts['backend_status'], 'SUCCEEDED', facts['warnings'])
                self.assertTrue(Path(facts['backend_ref']).is_file())
                self.assertEqual(facts['observations']['parser_status'], 'PARTIAL' if broken else 'COMPLETE_XML', facts['warnings'])
                if not broken:
                    self.assertEqual(facts['observations']['electronic_converged'], nelm != 1)
                    self.assertAlmostEqual(facts['observations']['steps'][0]['energy_zero_eV'], -0.9)
                    self.assertTrue((root / 'analysis/parser/energy.png').is_file())
                    self.assertTrue((root / 'analysis/parser/ionic_steps.csv').is_file())
                else:
                    self.assertIsNone(facts['observations']['electronic_converged'])


if __name__ == '__main__':
    unittest.main()
