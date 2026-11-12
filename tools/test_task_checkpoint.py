import json
from pathlib import Path
import unittest
from test_environment_check import runtime_directory
from task_checkpoint import checkpoint
from task_resume import resume

class CheckpointTests(unittest.TestCase):
    def navigation(self, root):
        plan = root / 'plan.json'
        plan.write_text(json.dumps({'task_id': 'task', 'note': 'engineering fixture'}))
        path = root / 'navigation.json'
        value = {'schema': 'dft-task-navigation/v1', 'task_id': 'task', 'update_reason': 'delivery',
                 'sources': [{'role': 'plan', 'path': 'plan.json'}], 'open_questions': ['Scientific choice remains unresolved'],
                 'next_action': 'Review the original decision', 'next_action_owner': 'route owner'}
        path.write_text(json.dumps(value))
        return path, plan, value

    def test_navigation_roundtrip_and_changed_source_blocks_card(self):
        from task_checkpoint import link_task_card
        with runtime_directory() as temp:
            root = Path(temp); receipt, _ = self.fixture(root)
            navigation, plan, _ = self.navigation(root)
            record = root / 'continue.json'
            checkpoint(receipt, root, 'task', 'executor', record, navigation=navigation)
            result = resume(record, root)
            self.assertEqual(result['navigation']['recorded_next_action'], 'Review the original decision')
            self.assertEqual(result['next_action'], 'VERIFY_EXECUTION_AUTHORITY')
            self.assertFalse(result['execution_authorized'])
            card = root / 'card.md'
            card.write_text('History\n<!-- AGENT_TASK_CARD_BEGIN:task -->\nQuestion\n<!-- AGENT_TASK_CARD_END:task -->\nOther task\n')
            link_task_card(card, record, root)
            before = card.read_text(encoding='utf-8')
            self.assertIn('plan.json', before)
            self.assertIn('Question', before)
            self.assertTrue(before.endswith('Other task\n'))
            plan.write_text('{}')
            self.assertEqual(resume(record, root)['status'], 'BLOCKED')
            with self.assertRaises(ValueError): link_task_card(card, record, root)
            self.assertEqual(card.read_text(encoding='utf-8'), before)

    def test_navigation_source_missing_and_navigation_changed(self):
        for mutate in ('missing', 'navigation'):
            with self.subTest(mutate=mutate), runtime_directory() as temp:
                root = Path(temp); receipt, _ = self.fixture(root)
                navigation, plan, value = self.navigation(root)
                record = root / 'continue.json'
                checkpoint(receipt, root, 'task', 'executor', record, navigation=navigation)
                if mutate == 'missing': plan.unlink()
                else:
                    value['next_action'] = 'Changed'; navigation.write_text(json.dumps(value))
                self.assertEqual(resume(record, root)['status'], 'BLOCKED')

    def test_navigation_copy_cannot_be_changed_independently(self):
        with runtime_directory() as temp:
            root = Path(temp); receipt, _ = self.fixture(root)
            navigation, _, _ = self.navigation(root); record = root / 'continue.json'
            value = checkpoint(receipt, root, 'task', 'executor', record, navigation=navigation)
            value['navigation']['recorded_next_action'] = 'Launch without review'
            record.write_text(json.dumps(value))
            result = resume(record, root)
            self.assertIn('NAVIGATION_BINDING_CHANGED', result['problems'])
            self.assertFalse(result['execution_authorized'])

    def test_navigation_rejects_task_escape_unknown_role_and_duplicates(self):
        for mutate in ('task', 'source_task', 'escape', 'role', 'duplicate', 'event'):
            with self.subTest(mutate=mutate), runtime_directory() as temp:
                root = Path(temp); receipt, _ = self.fixture(root)
                navigation, plan, value = self.navigation(root)
                if mutate == 'task': value['task_id'] = 'other'
                elif mutate == 'source_task': plan.write_text(json.dumps({'task_id': 'other'}))
                elif mutate == 'escape': value['sources'][0]['path'] = '../outside'
                elif mutate == 'role': value['sources'][0]['role'] = 'unreviewed_role'
                elif mutate == 'duplicate': value['sources'].append(value['sources'][0])
                else: value['update_reason'] = 'silent_refresh'
                navigation.write_text(json.dumps(value))
                record = root / 'continue.json'
                with self.assertRaises(ValueError): checkpoint(receipt, root, 'task', 'executor', record, navigation=navigation)
                self.assertFalse(record.exists())

    def fixture(self, root):
        path = root / "receipt.json"
        value = {"schema": "dft-preparation-handoff/v1", "task_id": "task", "status": "LOCAL_PACKAGE_CHECKED", "blockers": []}
        path.write_text(json.dumps(value))
        return path, value
    def test_preparation_to_resume_and_mutation(self):
        with runtime_directory() as temp:
            root = Path(temp); path, value = self.fixture(root); output = root / "continuation.json"
            checkpoint(path, root, "task", "executor", output)
            self.assertEqual(resume(output, root)["next_action"], "VERIFY_EXECUTION_AUTHORITY")
            value["status"] = "BLOCKED"; path.write_text(json.dumps(value))
            self.assertEqual(resume(output, root)["status"], "BLOCKED")
    def test_failure_identity_and_overwrite(self):
        with runtime_directory() as temp:
            root = Path(temp); path, value = self.fixture(root); output = root / "continuation.json"
            with self.assertRaises(ValueError): checkpoint(path, root, "other", "executor", output)
            checkpoint(path, root, "task", "executor", output)
            with self.assertRaises(FileExistsError): checkpoint(path, root, "task", "executor", output)
            value["status"] = "BLOCKED"; path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): checkpoint(path, root, "task", "executor", root / "failed.json")
    def test_analysis_backend_failure_not_checkpointed(self):
        with runtime_directory() as temp:
            root = Path(temp); path, value = self.fixture(root)
            path.write_text(json.dumps({"schema": "dft-analysis-facts/v1", "task_id": "task", "backend_status": "FAILED"}))
            with self.assertRaises(ValueError): checkpoint(path, root, "task", "executor", root / "next.json")

    def test_analysis_plan_change_blocks_even_when_receipt_unchanged(self):
        import hashlib
        with runtime_directory() as temp:
            root=Path(temp);plan=root/'plan.json';plan.write_bytes(b'original')
            facts=root/'facts.json';facts.write_text(json.dumps({'schema':'dft-analysis-facts/v1','task_id':'task','backend_status':'SUCCEEDED','plan_ref':str(plan),'plan_sha256':hashlib.sha256(b'original').hexdigest()}))
            output=root/'continue.json';checkpoint(facts,root,'task','executor',output)
            self.assertEqual(resume(output,root)['status'],'CONTEXT_RESTORED')
            plan.write_bytes(b'changed')
            self.assertEqual(resume(output,root)['status'],'BLOCKED')
            with self.assertRaises(ValueError):checkpoint(facts,root,'task','executor',root/'next.json')

    def test_card_link_preserves_science_and_other_tasks(self):
        from task_checkpoint import link_task_card
        with runtime_directory() as temp:
            root=Path(temp);receipt, _=self.fixture(root);record=root/'continue.json'
            checkpoint(receipt,root,'task','executor',record)
            card=root/'current.md'
            original='History\n<!-- AGENT_TASK_CARD_BEGIN:task -->\nResearch question stays\n<!-- AGENT_TASK_CARD_END:task -->\nOther task stays\n'
            card.write_text(original,encoding='utf-8')
            link_task_card(card,record,root)
            content=card.read_text(encoding='utf-8')
            self.assertIn('Research question stays',content)
            self.assertTrue(content.endswith('Other task stays\n'))
            link_task_card(card,record,root)
            self.assertEqual(card.read_text(encoding='utf-8').count('CONTINUATION_BEGIN'),1)

    def test_startup_checkpoint_only_restores_historical_query_action(self):
        with runtime_directory() as temp:
            root=Path(temp);receipt=root/'startup.json'
            receipt.write_text(json.dumps({'schema':'dft-execution-handoff/v1','identity':{'task_id':'task'},'startup_result':'STARTED_WITH_OUTPUT_EVIDENCE','blockers':[]}))
            output=root/'continue.json';checkpoint(receipt,root,'task','executor',output)
            result=resume(output,root)
            self.assertEqual(result['next_action'],'QUERY_STATUS_ON_REQUEST')
            self.assertFalse(result['execution_authorized'])
