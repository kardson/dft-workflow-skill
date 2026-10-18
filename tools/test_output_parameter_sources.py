"""Parameter assignment grammar and actual-case input regressions."""
import json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
from test_environment_check import runtime_directory

sys.path.insert(0,os.environ.get('DFT_TOOL_ROOT',str(Path(__file__).resolve().parents[1]/'vasp_toolchain')))
from progress_snapshot import parse_outcar_parameters
from vasp_executor import postcheck


class OutputParameterTests(unittest.TestCase):
    def test_effective_assignment_excludes_explanation_and_comments(self):
        result=parse_outcar_parameters('Effective parameters\n ISMEAR = 0; SIGMA = .05 broadening in eV\n EFERMI_NEDOS = 21; number of points for ismear=-14 and -15\n # ISMEAR = -5\n ENCUT = 450.0 eV 33.07 Ry 5.75 a.u.\n',Path('OUTCAR'))
        smear=result['fields']['ISMEAR']['observations']
        self.assertEqual([row['value'] for row in smear],[0])
        self.assertEqual(result['fields']['SIGMA']['observations'][0]['raw_value'],'.05')
        self.assertEqual(result['fields']['ENCUT']['observations'][0]['raw_value'],'450.0')

    def test_conflicting_actual_assignments_are_all_retained(self):
        result=parse_outcar_parameters('Effective parameters\nISMEAR = 0; ISMEAR = -5\n',Path('OUTCAR'))
        self.assertEqual([row['value'] for row in result['fields']['ISMEAR']['observations']],[0,-5])

    def test_postcheck_reads_case_incar_outside_required_outputs(self):
        with runtime_directory() as directory:
            root=Path(directory);manifest=root/'input_manifest.json'
            manifest.write_text(json.dumps({'route':'vasp','incar':{'ENCUT_eV':450}}),encoding='utf-8')
            (root/'INCAR').write_text('ENCUT=450\n',encoding='utf-8')
            (root/'OUTCAR').write_text('Effective parameters\nENCUT = 450.000000\nGeneral timing and accounting informations\n',encoding='utf-8')
            requirements={'kind':'static','required_files':['OUTCAR'],'nonempty_files':['OUTCAR'],'markers':[{'name':'end','category':'program','file':'OUTCAR','pattern':'General timing','required':True},{'name':'scf','category':'electronic','file':'OUTCAR','pattern':'EDIFF reached','required':True}]}
            with patch('vasp_executor_core.validate_inputs',return_value={'passed':True,'observations':{}}):
                result=postcheck(manifest,root,root,requirements)
            field=result['parameter_comparison']['fields']['ENCUT_eV']
            self.assertEqual(field['actual_incar']['value'],450)
            self.assertEqual(field['comparison'],'MATCH')


if __name__=='__main__':unittest.main()
