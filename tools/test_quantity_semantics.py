"""Quantity semantics engineering fixtures; no production files or calculations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from quantity_semantics import compare, convert_stress, main
from practical_geometry import free_force_components
from scientific_helpers import reference_energy


class QuantityTests(unittest.TestCase):
    def energy(self):
        return dict(unit='eV', backend='VASP', functional='PBE', paw_environment='engineering',
                    energy_basis='E0', reference_state='explicit reference', normalization='total')

    def test_metadata_match_unknown_and_incompatible(self):
        a = self.energy()
        self.assertEqual(compare('energy', a, dict(a))['state'], 'MATCH')
        for key, value in [('backend', 'CASTEP'), ('paw_environment', 'other'), ('energy_basis', 'F'),
                           ('reference_state', 'other'), ('normalization', 'per_atom')]:
            with self.subTest(key=key):
                self.assertEqual(compare('energy', a, {**a, key: value})['state'], 'INCOMPATIBLE')
        self.assertEqual(compare('energy', {}, {})['state'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(compare('energy', {**a, 'functional': None}, a)['state'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(compare('energy', {'applicable': False, 'not_applicable_reason': 'no energy task'},
                                 {'applicable': False, 'not_applicable_reason': 'no energy task'})['state'], 'NOT_APPLICABLE')

    def test_potential_windows_and_unaligned_reference(self):
        metadata = dict(unit='eV', axis='z', potential_component='LOCPOT', reference_zero='explicit aligned zero', sampling_window=[3, 5])
        self.assertEqual(compare('potential', metadata, dict(metadata))['state'], 'MATCH')
        self.assertEqual(compare('potential', metadata, {**metadata, 'axis': 'x'})['state'], 'INCOMPATIBLE')
        self.assertEqual(compare('potential', metadata, {**metadata, 'sampling_window': [4, 6]})['state'], 'INCOMPATIBLE')
        raw = {**metadata, 'reference_zero': 'SOURCE_LOCPOT_UNSHIFTED'}
        self.assertEqual(compare('potential', raw, dict(raw))['state'], 'INSUFFICIENT_EVIDENCE')

    def test_force_mapping_and_constraints_must_match(self):
        meta = dict(unit='eV/angstrom', coordinate_basis='Cartesian', constraint_basis='lattice_directions', atom_mapping=[1, 2], constraints=[[True]*3, [False]*3], cell_basis=[[1,0,0],[0,1,0],[0,0,1]])
        self.assertEqual(compare('force', meta, copy.deepcopy(meta))['state'], 'MATCH')
        self.assertEqual(compare('force', meta, {**meta, 'atom_mapping': [2, 1]})['state'], 'INCOMPATIBLE')
        self.assertEqual(compare('force', meta, {**meta, 'constraints': [[True]*3]*2})['state'], 'INCOMPATIBLE')

    def test_surface_normalization_and_legacy_unknown(self):
        metadata=dict(unit='eV/angstrom^2',energy_basis='E0',reference_state='explicit reference',area_unit='angstrom^2',surface_count=2,coverage='explicit coverage',termination='explicit termination')
        self.assertEqual(compare('surface',metadata,dict(metadata))['state'],'MATCH')
        for key, value in [('surface_count',1),('coverage','other'),('termination','other'),('surface_count',True)]:
            self.assertEqual(compare('surface',metadata,{**metadata,key:value})['state'],'INCOMPATIBLE')
        self.assertEqual(compare('surface',{}, {})['state'],'INSUFFICIENT_EVIDENCE')

    def test_mixed_skew_and_rotated_constraints_are_suppressed(self):
        forces = np.array([[1.,2.,3.], [4.,5.,6.]])
        masks = np.array([[False]*3, [True,False,True]])
        output, state = free_force_components(forces, masks, np.eye(3))
        np.testing.assert_array_equal(output, [[0,0,0],[4,0,6]])
        for cell in (np.array([[1,.2,0],[0,1,0],[0,0,1]]), np.array([[0,1,0],[-1,0,0],[0,0,1]])):
            self.assertIsNone(free_force_components(forces, masks, cell)[0])
            full = np.array([[False]*3,[True]*3])
            self.assertIsNotNone(free_force_components(forces, full, cell)[0])
        with self.assertRaises(ValueError): free_force_components([[float('nan'),0,0]], [[True]*3], np.eye(3))

    def test_stress_conversion_matches_ase_vasp_sign_order_and_units(self):
        from ase import units
        value = dict(values=[1,2,3,4,5,6], unit='kbar', sign_convention='compression_positive',
                     component_order=['xx','yy','zz','xy','yz','xz'], coordinate_basis='Cartesian')
        original = copy.deepcopy(value)
        result = convert_stress(value)
        np.testing.assert_allclose(result['converted']['values'], -np.array([1,2,3,5,6,4])*0.1*units.GPa)
        self.assertEqual(value, original)
        self.assertEqual(convert_stress(result['converted'])['converted'], result['converted'])
        for key, replacement in [('unit','unknown'), ('sign_convention',None), ('component_order',['xx']*6), ('values',[True]*6)]:
            with self.assertRaises(ValueError): convert_stress({**value,key:replacement})

    def test_actual_xml_parser_retains_raw_force_but_suppresses_skew_mask(self):
        from test_analysis import synthetic_xml
        from result_analysis import analyze
        from scientific_helpers import geometry_comparison
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; source.mkdir()
            poscar=source/'POSCAR'
            poscar.write_text('Engineering Si\n1\n3 .4 0\n0 3 0\n0 0 3\nSi\n1\nSelective Dynamics\nDirect\n0 0 0 T F T\n')
            xml=source/'vasprun.xml';xml.write_text(synthetic_xml().replace('<v>3 0 0</v>','<v>3 .4 0</v>'))
            report=analyze(source,root/'analysis')
            self.assertEqual(report['status'],'COMPLETE_XML')
            self.assertIsNone(report['steps'][0]['max_free_force_eV_A'])
            self.assertAlmostEqual(report['steps'][0]['max_force_eV_A'],.01)
            self.assertIn('FREE_FORCE_SUPPRESSED:UNSUPPORTED_PARTIAL_CONSTRAINTS',report['semantic_limitations'])
            xml.write_text(xml.read_text().replace('<v>0 0 0</v>','<v>0.1 0 0</v>'))
            with self.assertRaisesRegex(ValueError,'final geometry differs'):
                geometry_comparison(poscar,poscar,[],[1],[1],[0,0,1],xml)

    def reference(self, root):
        contract = dict(schema='vasp-fixed-geometry-reference/v1', energy_basis='E0', interpretation='FIXED_GEOMETRY_INTERACTION',
                        approved_differences=['atom_box','kpoints','spin'], sources={})
        for role, energy in [('combined',-10.),('clean',-8.),('atom',-1.)]:
            folder = root/'returns'/role; folder.mkdir(parents=True)
            record = folder/'record.json'; status = folder/'status.json'; manifest = folder/'input.json'
            record.write_text(json.dumps(dict(schema='vasp-result-record/v1', execution_id=role, case_id='case',attempt=1,
                source=str(folder/'case'),result_state='COMPLETE_XML',electronic_converged=True,energy={'E0_eV':energy})))
            status.write_text(json.dumps({'execution_id':role,'status':'COMPLETED','cases':[{'path':'/remote/'+role}]}))
            manifest.write_text(json.dumps({'route':'vasp','paw_identity':{'paw_family':'PBE','environment_id':'engineering',
                'ordered_roles':['Ga'],'Ga':{'component_sha256':'a'*64}}}))
            contract['sources'][role]=dict(record_path=str(record),execution_id=role,case_id='case',attempt=1,
                input_manifest_path=str(manifest),status_path=str(status),status_case_path='/remote/'+role)
        return contract

    def test_reference_rejects_nonfinite_unconverged_wrong_identity_and_incompatible_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = self.reference(Path(directory))
            result = reference_energy(contract)
            self.assertEqual(result['interaction_E0_eV'], -1.)
            self.assertEqual(result['formal_comparability'], 'INSUFFICIENT_EVIDENCE')
            path=Path(contract['sources']['combined']['record_path']); original=json.loads(path.read_text())
            for change in ({'electronic_converged':False},{'energy':{'E0_eV':float('nan')}},{'attempt':2}):
                path.write_text(json.dumps({**original,**change}))
                with self.assertRaises(ValueError): reference_energy(contract)
            path.write_text(json.dumps(original))
            contract['quantity_metadata']={role:self.energy() for role in contract['sources']}
            self.assertEqual(reference_energy(contract)['formal_comparability'],'REQUIRES_ROUTE_REVIEW')
            contract['quantity_metadata']['clean']['functional']='other'
            with self.assertRaises(ValueError): reference_energy(contract)

    def test_offline_cli_writes_unknown_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'request.json'; output=root/'result.json'
            source.write_text(json.dumps({'schema':'dft-quantity-request/v1','operation':'compare','quantity':'energy','left':{},'right':{}}))
            self.assertEqual(main(['--request',str(source),'--output',str(output)]),1)
            with self.assertRaises(ValueError): main(['--request',str(source),'--output',str(output)])


if __name__ == '__main__': unittest.main()
