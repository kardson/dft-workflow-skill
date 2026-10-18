import json
from pathlib import Path
import unittest
from test_environment_check import runtime_directory
from task_checkpoint import checkpoint
from task_resume import resume

class CheckpointTests(unittest.TestCase):
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
