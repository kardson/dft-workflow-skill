from pathlib import Path
import unittest
import json
import subprocess
from unittest.mock import patch
from test_environment_check import runtime_directory
from legacy_import import import_outputs

class LegacyTests(unittest.TestCase):
    def test_backend_failure_is_not_partial_success(self):
        with runtime_directory() as temp:
            root=Path(temp); source=root/'old'; source.mkdir()
            (source/'OSZICAR').write_text('historical')
            (source/'POTCAR').write_text('must not copy')
            result=import_outputs(source,root/'import',root,root,'missing-interpreter')
            self.assertEqual(result['backend_status'],'FAILED')
            self.assertFalse(result['controlled_workflow'])
            self.assertFalse((root/'import/raw/POTCAR').exists())
            self.assertEqual((source/'OSZICAR').read_text(),'historical')
    def test_budget_and_source_overlap(self):
        with runtime_directory() as temp:
            root=Path(temp); source=root/'old'; source.mkdir(); (source/'OUTCAR').write_text('12345')
            with self.assertRaises(ValueError): import_outputs(source,root/'new',root,root,'python',1)
            self.assertFalse((root/'new').exists())
            with self.assertRaises(ValueError): import_outputs(source,source/'import',root,root,'python')

    def test_backend_record_mismatch_and_mutated_snapshot_fail(self):
        for mutation in ("summary", "snapshot"):
            with self.subTest(mutation=mutation), runtime_directory() as temp:
                root=Path(temp); source=root/'old'; source.mkdir()
                (source/'OSZICAR').write_text('historical')
                output=root/'import'
                summary={'schema':'vasp-practical-analysis/v1','status':'PARTIAL','steps':[]}
                def run(argv, **kwargs):
                    parser=output/'parser';parser.mkdir()
                    saved=dict(summary)
                    if mutation=='summary': saved['status']='COMPLETE_XML'
                    (parser/'summary.json').write_text(json.dumps(saved))
                    if mutation=='snapshot': (output/'raw/OSZICAR').write_text('changed')
                    return subprocess.CompletedProcess(argv,0,'LEGACY_RESULT:'+json.dumps(summary),'')
                with patch('legacy_import.subprocess.run',side_effect=run):
                    value=import_outputs(source,output,root,root,'synthetic')
                self.assertEqual(value['backend_status'],'FAILED')
                self.assertNotIn('observations',value)
                self.assertIn('解析未成功', (output/'README.md').read_text(encoding='utf-8'))

    def test_success_requires_saved_parser_record(self):
        with runtime_directory() as temp:
            root=Path(temp);source=root/'old';source.mkdir();(source/'OSZICAR').write_text('historical')
            output=root/'import'
            summary={'schema':'vasp-practical-analysis/v1','status':'PARTIAL','steps':[{'free_energy_eV':-1}]}
            def run(argv, **kwargs):
                parser=output/'parser';parser.mkdir();(parser/'summary.json').write_text(json.dumps(summary))
                return subprocess.CompletedProcess(argv,0,'LEGACY_RESULT:'+json.dumps(summary),'')
            with patch('legacy_import.subprocess.run',side_effect=run):
                value=import_outputs(source,output,root,root,'synthetic')
            self.assertEqual(value['backend_status'],'SUCCEEDED')
            self.assertEqual(value['observations']['steps'],summary['steps'])
            self.assertFalse(value['controlled_workflow'])
