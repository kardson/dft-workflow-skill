"""Meaningful geometry, provenance, source protection and archive boundaries."""
import copy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

import numpy as np
from ase.build import bulk
from ase.io import write
from local_structure import load_structure, prepare, preview
from local_structure import candidates
from local_delivery import archive_static


class LocalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'POSCAR'
        write(self.source, bulk('Si', 'diamond', a=5.4, cubic=True), format='vasp', direct=True)
        self.original = self.source.read_bytes()
        self.config = {'source_class': 'engineering_fixture', 'provenance': 'Artificial geometry test',
                       'source_format': 'vasp', 'source_file': 'POSCAR', 'minimum_separation_angstrom': 0.2}

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.temporary.cleanup()

    def slab(self):
        return {**self.config, 'operation': 'slab', 'miller': [0, 0, 1], 'layers': 2,
                'in_plane_repeats': [1, 1], 'vacuum_below_angstrom': 2., 'vacuum_above_angstrom': 8.,
                'plane_tolerance_angstrom': 1e-5, 'freeze_bottom_planes': 1,
                'trim_bottom_planes': 0, 'trim_top_planes': 0,
                'termination_description': 'Artificial bare cut'}

    def test_bulk_and_slab_are_explicit_candidates_with_mapping_and_masks(self):
        result = prepare({**self.config, 'operation': 'bulk', 'repeats': [2, 1, 1]}, self.root, self.root / 'bulk')
        self.assertEqual(result['atom_count'], 16)
        self.assertEqual(result['scientific_acceptance'], 'NOT_GRANTED')
        prepare(self.slab(), self.root, self.root / 'slab')
        atoms, _ = load_structure(self.root / 'slab/POSCAR')
        fixed = ~atoms.arrays['vasp_free'].any(axis=1)
        self.assertTrue(fixed.any()); self.assertTrue((~fixed).any())
        self.assertTrue((self.root / 'slab/atom_mapping.csv').is_file())
        self.assertAlmostEqual(atoms.positions[:, 2].min(), 2.)
        self.assertAlmostEqual(atoms.cell[2, 2] - atoms.positions[:, 2].max(), 8.)

    def test_unresolved_fields_and_invalid_geometry_publish_nothing(self):
        for changes in ({'miller': None}, {'termination_description': None}, {'vacuum_above_angstrom': None},
                        {'minimum_separation_angstrom': 10.}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                prepare({**self.slab(), **changes}, self.root, self.root / 'invalid')
            self.assertFalse((self.root / 'invalid').exists())

    def test_explicit_plane_trimming_preserves_declared_vacuum_and_changes_termination(self):
        untrimmed = prepare(self.slab(), self.root, self.root / 'full')
        trimmed = prepare({**self.slab(), 'trim_bottom_planes': 1, 'trim_top_planes': 1}, self.root, self.root / 'trimmed')
        self.assertLess(trimmed['atom_count'], untrimmed['atom_count'])
        atoms, _ = load_structure(self.root / 'trimmed/POSCAR')
        self.assertAlmostEqual(atoms.positions[:, 2].min(), 2.)
        self.assertAlmostEqual(atoms.cell[2, 2] - atoms.positions[:, 2].max(), 8.)

    def test_pseudo_h_labels_and_mixed_flags_survive_repeat_and_preview(self):
        source = self.root / 'labeled'
        source.write_text('fixture\n1\n10 0 0\n0 10 0\n0 0 10\nSi H.25\n1 1\nSelective dynamics\nDirect\n0 0 0 T F T\n0.5 0.5 0.5 F F F\n')
        config = {**self.config, 'source_file': str(source), 'operation': 'bulk', 'repeats': [2, 1, 1]}
        prepare(config, self.root, self.root / 'labeled-bulk')
        atoms, _ = load_structure(self.root / 'labeled-bulk/POSCAR')
        self.assertEqual(atoms.arrays['vasp_label'].tolist(), ['Si', 'Si', 'H.25', 'H.25'])
        self.assertEqual(atoms.arrays['vasp_free'].tolist(), [[True, False, True]] * 2 + [[False, False, False]] * 2)
        image = preview(self.root / 'labeled-bulk/POSCAR', self.root / 'preview', 'ENGINEERING FIXTURE')
        self.assertTrue(Path(image['image']).is_file())
        self.assertIn('H.25', image['labels'])

    def test_fractional_cif_occupancy_is_not_silently_ordered(self):
        cif = self.root / 'partial.cif'
        cif.write_text('data_fixture\n_cell_length_a 5\n_cell_length_b 5\n_cell_length_c 5\n_cell_angle_alpha 90\n_cell_angle_beta 90\n_cell_angle_gamma 90\nloop_\n_atom_site_label\n_atom_site_type_symbol\n_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z\n_atom_site_occupancy\nSi1 Si 0 0 0 0.5\n')
        config = {**self.config, 'source_file': str(cif), 'source_format': 'cif', 'operation': 'bulk', 'repeats': [1, 1, 1]}
        with self.assertRaisesRegex(ValueError, 'occupancy'):
            prepare(config, self.root, self.root / 'ordered')
        self.assertFalse((self.root / 'ordered').exists())

    def test_interface_preserves_long_film_labels_and_reports_strain(self):
        substrate = self.root / 'substrate'
        film = self.root / 'film'
        base = 'fixture\n1\n4 0 0\n0 4 0\n0 0 10\n{label}\n1\nSelective dynamics\nDirect\n0.5 0.5 0.2 T T T\n'
        substrate.write_text(base.format(label='Si')); film.write_text(base.format(label='H.25'))
        config = {**self.config, 'operation': 'interface', 'substrate_file': str(substrate), 'film_file': str(film),
                  'lateral_match': 'strain_film_to_substrate', 'film_xy_shift_angstrom': [0., 0.],
                  'gap_angstrom': 2., 'vacuum_below_angstrom': 2., 'vacuum_above_angstrom': 8.}
        result = prepare(config, self.root, self.root / 'interface')
        atoms, _ = load_structure(self.root / 'interface/POSCAR')
        self.assertEqual(atoms.arrays['vasp_label'].tolist(), ['Si', 'H.25'])
        self.assertEqual(result['details']['film_lateral_strain'], [0., 0.])

    def test_candidate_budget_and_partial_failure_are_visible(self):
        config = {'variants': [{'id': 'a', 'structure': {**self.config, 'operation': 'bulk', 'repeats': [1, 1, 1]}},
                               {'id': 'b', 'structure': {**self.slab(), 'miller': None}}]}
        with self.assertRaises(ValueError): candidates(config, self.root, self.root / 'budget', 1)
        self.assertFalse((self.root / 'budget').exists())
        with self.assertRaises(ValueError): candidates(config, self.root, self.root / 'partial', 2)
        record = json.loads((self.root / 'partial/candidates.json').read_text())
        self.assertEqual(record['status'], 'FAILED_PARTIAL')
        self.assertEqual(len(record['candidates']), 1)

    def test_static_archive_rejects_mutated_input(self):
        from test_static_delivery_builder import _source_files, _manifest
        from static_delivery_builder import build_static_delivery
        source = self.root / 'archive-source'; source.mkdir(); _source_files(source)
        spec = self.root / 'approved.json'; spec.write_text(json.dumps(_manifest(source)))
        package = self.root / 'package'; build_static_delivery(spec, source, package)
        result = archive_static(package, self.root / 'inputs.zip', self.root / 'archive.json')
        self.assertTrue(result['member_bytes_verified'])
        (package / 'INCAR').write_text('ENCUT=1\n')
        with self.assertRaises(Exception): archive_static(package, self.root / 'changed.zip', self.root / 'changed.json')
        self.assertFalse((self.root / 'changed.zip').exists())

    def test_html_uses_controlled_facts_escapes_text_and_rejects_changed_sources(self):
        admin = Path(__file__).resolve().parents[1] / 'agent_workflow'
        sys.path.insert(0, str(admin))
        import analysis
        from test_analysis import fixture, review
        from local_delivery import controlled_report
        fixture(self.root)
        facts = analysis.parse(self.root / 'recovery.json', self.root / 'plan.json', 'static1',
            self.root / 'spec.json', self.root / 'requirements.json', self.root,
            Path(__file__).resolve().parent, sys.executable, self.root / 'analysis',
            preparation=self.root / 'preparation.json')
        self.assertEqual(facts['backend_status'], 'SUCCEEDED')
        review(self.root)
        reviewed = json.loads((self.root / 'review.json').read_text())
        reviewed['answer'] = 'Engineering <script>unsafe()</script>'
        (self.root / 'review.json').write_text(json.dumps(reviewed))
        result = controlled_report(self.root / 'analysis/facts.json', self.root / 'review.json', self.root, self.root / 'report')
        page = Path(result['html_report']).read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)
        self.assertIn('data:image/png;base64,', page)
        (self.root / 'returned/OUTCAR').write_text('changed source')
        with self.assertRaises(ValueError):
            controlled_report(self.root / 'analysis/facts.json', self.root / 'review.json', self.root, self.root / 'bad-report')
        self.assertFalse((self.root / 'bad-report').exists())


if __name__ == '__main__':
    unittest.main()
