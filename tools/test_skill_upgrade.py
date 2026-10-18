import hashlib
import json
from pathlib import Path
import unittest
from skill_upgrade import stage_upgrade
from test_environment_check import runtime_directory

class UpgradeTests(unittest.TestCase):
    def fixture(self, root):
        new=root/'candidate';new.mkdir();(new/'SKILL.md').write_text('new')
        (new/'release_manifest.json').write_text(json.dumps({'schema':'public-release-allowlist/v1','files':[{'path':'SKILL.md','sha256':hashlib.sha256(b'new').hexdigest()}]}))
        old=root/'installed';old.mkdir();(old/'SKILL.md').write_text('old local routing');(old/'local-adapter.md').write_text('private adapter')
        return new,old
    def test_stage_preserves_old_and_adapter(self):
        with runtime_directory() as temp:
            root=Path(temp);new,old=self.fixture(root)
            result=stage_upgrade(new,old,root/'stage')
            self.assertEqual((old/'SKILL.md').read_text(),'old local routing')
            self.assertEqual((root/'stage/preserved-local/local-adapter.md').read_text(),'private adapter')
            self.assertEqual(result['compatibility'],'UNVERIFIED')
            self.assertFalse(result['running_tasks_migrated'])
    def test_changed_candidate_and_overlap_rejected(self):
        with runtime_directory() as temp:
            root=Path(temp);new,old=self.fixture(root)
            with self.assertRaises(ValueError):stage_upgrade(new,old,old/'stage')
            (new/'SKILL.md').write_text('changed')
            with self.assertRaises(ValueError):stage_upgrade(new,old,root/'stage')
            self.assertFalse((root/'stage').exists())
