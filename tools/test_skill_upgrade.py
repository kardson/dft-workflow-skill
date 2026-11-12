import hashlib
import json
from pathlib import Path
import unittest
from skill_upgrade import stage_upgrade
from test_environment_check import runtime_directory

class UpgradeTests(unittest.TestCase):
    def test_actual_adapter_names_and_unlisted_configuration_are_reported(self):
        with runtime_directory() as temp:
            root=Path(temp);new,old=self.fixture(root)
            (old/'project-local.md').write_text('project-local routing')
            (old/'LOCAL-SOURCE.md').write_text('maintenance source')
            (old/'custom-config.json').write_text('{}')
            result=stage_upgrade(new,old,root/'stage',preserve=('project-local.md','LOCAL-SOURCE.md'))
            self.assertEqual((root/'stage/preserved-local/project-local.md').read_text(),'project-local routing')
            self.assertIn('LOCAL-SOURCE.md',result['preserved_local_sha256'])
            self.assertIn('custom-config.json',result['installed_top_level_unlisted'])
            self.assertFalse((root/'stage/skill/custom-config.json').exists())

    def test_installed_local_modification_is_reported_without_replacing_it(self):
        with runtime_directory() as temp:
            root=Path(temp);new,old=self.fixture(root)
            (old/'release_manifest.json').write_bytes((new/'release_manifest.json').read_bytes())
            result=stage_upgrade(new,old,root/'stage')
            self.assertEqual(result['installed_manifest_state'],'UNVERIFIED_OR_LOCALLY_MODIFIED')
            self.assertEqual((old/'SKILL.md').read_text(),'old local routing')

    def test_candidate_unsafe_path_and_duplicate_refused(self):
        for name in ('../outside', 'SKILL.md'):
            with self.subTest(name=name), runtime_directory() as temp:
                root=Path(temp);new,old=self.fixture(root)
                path=new/'release_manifest.json';value=json.loads(path.read_text())
                value['files'].append({'path':name,'sha256':'0'*64});path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):stage_upgrade(new,old,root/'stage')
                self.assertFalse((root/'stage').exists())
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
