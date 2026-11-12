"""Explicit engineering mappings; no simulation/backend invocation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import planning
from test_planning import fixture
from test_environment_check import runtime_directory


def catalog():
    return {'schema': 'vasp-agent-capabilities/v1', 'commands': [
        {'name': 'results', 'cli': ['vasp_workflow.py', 'results'],
         'entry_mode_choices_from_parser': ['results'], 'supported_scope': 'Parse existing outputs',
         'effects': {'remote_compute_control': False}}]}


def tool_step():
    return {'command': 'results', 'purpose': 'engineering question',
            'inputs': [{'artifact': 'model', 'source_ref': 'model.txt', 'kind': 'metadata'}], 'outputs': ['OUTCAR'],
            'completion_check': 'Verify actual artifact', 'acceptance_check': 'Route review required'}


class MappingTests(unittest.TestCase):
    def setup_plan(self, root):
        value = fixture(root)
        value['stages'][0]['tool_steps'] = [tool_step()]
        return value

    def test_mapping_does_not_mutate_or_authorize_and_old_plan_is_compatible(self):
        with runtime_directory() as directory:
            value = self.setup_plan(Path(directory)); original = copy.deepcopy(value)
            self.assertNotIn('capability_mapping', planning.validate(value, directory))
            result = planning.validate(value, directory, capabilities=catalog())
            self.assertEqual(result['status'], 'READY_FOR_USER_REVIEW')
            self.assertFalse(result['execution_authorized'])
            self.assertEqual(value, original)
            self.assertEqual(result['capability_mapping']['stages'][0]['support_state'], 'API_PRESENT_NO_VALIDATION_EVIDENCE')

    def test_unavailable_missing_mapping_and_stale_catalog_commands(self):
        with runtime_directory() as directory:
            value = self.setup_plan(Path(directory))
            value['stages'][0]['tool_steps'][0]['command'] = 'invented'
            self.assertTrue(planning.validate(value, directory, capabilities=catalog())['blockers'])
            value['stages'][0].pop('tool_steps')
            self.assertIn('static1:CAPABILITY_MAPPING_MISSING', planning.validate(value, directory, capabilities=catalog())['blockers'])
            value = self.setup_plan(Path(directory)); cap = catalog(); cap['commands'][0]['entry_mode_choices_from_parser'] = []
            self.assertTrue(planning.validate(value, directory, capabilities=cap)['blockers'])

    def test_planned_artifact_and_dependency_must_match(self):
        with runtime_directory() as directory:
            root = Path(directory); value = self.setup_plan(root)
            second = copy.deepcopy(value['stages'][0]); second['id'] = 'static2'
            second['tool_steps'][0]['inputs'] = [{'artifact': 'OUTCAR', 'source_stage': 'static1', 'required_contract_matches': ['paw']}]
            value['stages'].append(second); value['dependency_plan_ref'] = 'dependency.json'
            dependency = {'schema': 'vasp-property-plan/v1', 'execution_authorized': False,
                          'nodes': [{'stage_id': 'static1', 'depends_on': [], 'contract_identities': {'paw': 'same'}},
                                    {'stage_id': 'static2', 'depends_on': ['static1'], 'contract_identities': {'paw': 'same'}}]}
            (root / 'dependency.json').write_text(json.dumps(dependency))
            result = planning.validate(value, root, capabilities=catalog())
            self.assertEqual(result['status'], 'READY_FOR_USER_REVIEW')
            self.assertEqual(result['capability_mapping']['stages'][1]['inputs'][0]['state'], 'PLANNED_NOT_MATERIALIZED')
            gap = result['capability_mapping']['stages'][1]['upstream_acceptance_gaps'][0]
            self.assertEqual(gap['status'], 'UPSTREAM_NOT_MATERIALIZED')
            self.assertTrue(gap['route_verification_required'])
            dependency['nodes'][1]['contract_identities']['paw'] = 'different'
            (root / 'dependency.json').write_text(json.dumps(dependency))
            self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])
            dependency['nodes'][1]['contract_identities']['paw'] = 'same'
            (root / 'dependency.json').write_text(json.dumps(dependency))
            value['stages'][0]['tool_steps'][0]['outputs'] = ['other']
            self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])
            dependency['nodes'][0]['depends_on'] = ['static2']; (root / 'dependency.json').write_text(json.dumps(dependency))
            with self.assertRaisesRegex(ValueError, 'cycle'): planning.validate(value, root, capabilities=catalog())

    def test_result_identity_completeness_convergence_and_contracts(self):
        with runtime_directory() as directory:
            root = Path(directory); value = self.setup_plan(root)
            ids = {'execution_id': 'engineering-exec', 'case_id': 'case', 'attempt': 1}
            contracts = {key: key + '-engineering' for key in ('geometry', 'environment', 'paw', 'restart')}
            source = {'schema': 'vasp-result-record/v1', **ids, 'run_state': 'COMPLETED', 'result_state': 'COMPLETE_XML',
                      'ionic_converged': True, 'contract_identities': contracts, 'engineering_fixture': True}
            item = {'artifact': 'CONTCAR', 'source_ref': 'result.json', 'kind': 'result', 'expected_identity': ids,
                    'required_acceptance_scope': 'CONVERGED_GEOMETRY', 'contract_identities': contracts}
            decision = root / 'decision.md'
            decision.write_text('Engineering decision reference only; route verification required.', encoding='utf-8')
            item.update(scientific_decision_ref='decision.md',
                        scientific_decision_sha256=hashlib.sha256(decision.read_bytes()).hexdigest())
            value['stages'][0]['tool_steps'][0]['inputs'] = [item]
            (root / 'result.json').write_text(json.dumps(source))
            self.assertFalse(planning.validate(value, root, capabilities=catalog())['blockers'])
            for field, change in [('execution_id', 'other'), ('attempt', True), ('ionic_converged', False),
                                  ('result_state', 'PARTIAL'), ('contract_identities', {}), ('schema', 'unsupported')]:
                with self.subTest(field=field):
                    bad = {**source, field: change}; (root / 'result.json').write_text(json.dumps(bad))
                    self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])

    def test_upstream_acceptance_needs_bound_evidence_and_remains_a_route_decision(self):
        with runtime_directory() as directory:
            root = Path(directory); value = self.setup_plan(root)
            ids = {'execution_id': 'exec', 'case_id': 'case', 'attempt': 1}
            contracts = {key: key for key in ('geometry', 'environment', 'paw', 'restart')}
            source = {'schema': 'vasp-result-record/v1', **ids, 'run_state': 'COMPLETED',
                      'result_state': 'COMPLETE_XML', 'contract_identities': contracts,
                      'scientific_acceptance': 'ACCEPTED'}  # A string alone cannot grant acceptance.
            (root / 'result.json').write_text(json.dumps(source), encoding='utf-8')
            item = {'artifact': 'OUTCAR', 'source_ref': 'result.json', 'kind': 'result',
                    'expected_identity': ids, 'required_acceptance_scope': 'COMPLETE_RESULT',
                    'contract_identities': contracts}
            value['stages'][0]['tool_steps'][0]['inputs'] = [item]
            result = planning.validate(value, root, capabilities=catalog())
            self.assertTrue(any('UPSTREAM_SCIENTIFIC_DECISION_MISSING' in b for b in result['blockers']))
            bound = result['capability_mapping']['stages'][0]['inputs'][0]
            self.assertEqual(bound['upstream_scientific_acceptance']['status'], 'MISSING_DECISION_EVIDENCE')
            decision = root / 'decision.json'
            decision.write_text(json.dumps({'task_id': value['task_id'], 'answer': 'Engineering fixture only'}), encoding='utf-8')
            item.update(scientific_decision_ref='decision.json',
                        scientific_decision_sha256=hashlib.sha256(decision.read_bytes()).hexdigest())
            result = planning.validate(value, root, capabilities=catalog())
            self.assertFalse(result['blockers'])
            gap = result['capability_mapping']['stages'][0]['upstream_acceptance_gaps'][0]
            self.assertEqual(gap['status'], 'REFERENCED_REQUIRES_ROUTE_VERIFICATION')
            self.assertEqual(gap['scientific_acceptance'], 'NOT_VERIFIED_BY_TOOL')
            self.assertTrue(gap['route_verification_required'])
            self.assertFalse(result['execution_authorized'])
            original = dict(item)
            for changes in ({'scientific_decision_sha256': '0' * 64},
                            {'scientific_decision_sha256': None},
                            {'scientific_decision_ref': '../decision.json'},
                            {'scientific_decision_ref': 'decision.json#/answer'}):
                item.clear(); item.update(original); item.update(changes)
                self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])
            item.clear(); item.update(original)
            decision.write_text(json.dumps({'task_id': 'other-task'}), encoding='utf-8')
            item['scientific_decision_sha256'] = hashlib.sha256(decision.read_bytes()).hexdigest()
            self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])
            for changes in ({'execution_id': 'other-exec'}, {'case_id': 'other-case'}, {'attempt': True}):
                decision.write_text(json.dumps({'task_id': value['task_id'], 'identity': {**ids, **changes}}), encoding='utf-8')
                item['scientific_decision_sha256'] = hashlib.sha256(decision.read_bytes()).hexdigest()
                self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])

    def test_source_identity_escape_and_missing_file(self):
        with runtime_directory() as directory:
            root = Path(directory); value = self.setup_plan(root)
            for ref in ('missing.json', '../escape.json', 'other.json'):
                (root / 'other.json').write_text(json.dumps({'task_id': 'other-task'}))
                value['stages'][0]['tool_steps'][0]['inputs'][0]['source_ref'] = ref
                self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])

    def test_validation_is_referenced_evidence_not_independent_pass(self):
        with runtime_directory() as directory:
            root = Path(directory); value = self.setup_plan(root); step = value['stages'][0]['tool_steps'][0]
            (root / 'validation.json').write_text(json.dumps({'engineering_fixture': True, 'status': 'PASSED'}))
            step.update(validation_refs=['validation.json'], validation_kind='ENGINEERING', validation_scope='fixture parsing only')
            result = planning.validate(value, root, capabilities=catalog())
            self.assertEqual(result['capability_mapping']['stages'][0]['support_state'], 'API_PRESENT_WITH_REFERENCED_VALIDATION')
            step['validation_kind'] = 'REMOTE_BACKEND'
            self.assertTrue(planning.validate(value, root, capabilities=catalog())['blockers'])

    def test_real_cli_reads_actual_catalog_without_invoking_backend(self):
        here = Path(__file__).resolve().parent
        default = here if (here / 'agent_capabilities.py').is_file() else here.parent / 'vasp_toolchain'
        tool_root = Path(os.environ.get('DFT_TOOL_ROOT', str(default)))
        with runtime_directory() as directory:
            root = Path(directory); value = self.setup_plan(root)
            value['stages'][0]['tool_steps'][0]['command'] = 'evidence'
            source = root / 'plan.json'; source.write_text(json.dumps(value))
            argv = [sys.executable, '-X', 'utf8', '-B', str(Path(planning.__file__)), '--plan', str(source),
                    '--workspace', str(root), '--output-dir', str(root / 'report'), '--tool-root', str(tool_root)]
            p = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            report = json.loads((root / 'report/plan_check.json').read_text(encoding='utf-8'))
            self.assertEqual(report['capability_mapping']['stages'][0]['command'], 'evidence')
            self.assertTrue((root / 'report/capabilities.json').is_file())


if __name__ == '__main__': unittest.main()
