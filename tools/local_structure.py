"""Explicit-source unrelaxed candidates and previews; no calculator or submission."""
from __future__ import annotations

import csv
import hashlib
import io
import importlib.metadata
import json
from pathlib import Path
import re

import numpy as np
from ase.io import read
from ase.neighborlist import neighbor_list
from progress_snapshot import parse_poscar
from practical_geometry import inspect_geometry


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fresh(path):
    path = Path(path).resolve()
    installation = Path(__file__).resolve().parent.parent
    if (installation / 'SKILL.md').is_file() and (path == installation or installation in path.parents):
        raise ValueError('Keep generated work outside the installed skill')
    if path.exists():
        raise ValueError('Use a new output directory; existing data is preserved')
    path.mkdir(parents=True)
    return path


def number(config, key, *, positive=False):
    value = config.get(key)
    if type(value) not in (int, float) or not np.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError('Explicit finite ' + key + ' required')
    return float(value)


def integers(value, length, *, zero=False):
    if not isinstance(value, list) or len(value) != length or any(type(x) is not int or x < (0 if zero else 1) for x in value):
        raise ValueError('Explicit integer list required')
    return value


def load_structure(path, fmt='vasp', source_id=0):
    path = Path(path).resolve(strict=True)
    initial_digest = digest(path)
    if fmt == 'vasp':
        text = path.read_text(encoding='utf-8-sig')
        parsed = parse_poscar(text, path)
        if not parsed['valid'] or parsed.get('species_unresolved'):
            raise ValueError('Explicit valid POSCAR labels and geometry required')
        lines = text.splitlines()
        lines[5] = ' '.join('H' if re.fullmatch(r'H(?:\.\d+|\d+(?:\.\d+)?)?', x) else x for x in parsed['species'])
        atoms = read(io.StringIO('\n'.join(lines) + '\n'), format='vasp')
        labels = [a['species'] for a in parsed['atoms']]
        masks = np.asarray([a['flags'] for a in parsed['atoms']], dtype=bool)
    elif fmt == 'cif':
        atoms = read(path, format='cif')
        for occupancy in atoms.info.get('occupancy', {}).values():
            if len(occupancy) != 1 or not np.isclose(float(next(iter(occupancy.values()))), 1.0):
                raise ValueError('Fractional or mixed CIF occupancy needs an explicit scientific model')
        labels = atoms.get_chemical_symbols()
        masks = np.ones((len(atoms), 3), dtype=bool)
        if atoms.constraints:
            raise ValueError('Constrained CIF import is outside this adapter')
    else:
        raise ValueError('Supported source formats are explicit vasp or cif')
    if not len(atoms) or not np.isfinite(atoms.positions).all() or not np.isfinite(atoms.cell.array).all() or atoms.cell.volume <= 0:
        raise ValueError('Finite nonempty positive-volume source required')
    atoms.set_constraint([])
    atoms.set_array('vasp_label', np.asarray(labels))
    atoms.set_array('vasp_free', masks)
    atoms.set_array('source_index_1based', np.arange(1, len(atoms) + 1))
    atoms.set_array('source_id', np.full(len(atoms), source_id, dtype=int))
    if digest(path) != initial_digest:
        raise ValueError('Source changed during structure inspection')
    return atoms, {'path': str(path), 'sha256': initial_digest, 'format': fmt}


def export_poscar(atoms, path, purpose):
    labels = atoms.arrays['vasp_label'].tolist()
    order = list(dict.fromkeys(labels))
    permutation = [i for label in order for i, value in enumerate(labels) if value == label]
    atoms = atoms[permutation]
    lines = [purpose + ' / UNRELAXED CANDIDATE', '1.0']
    lines += [' '.join(f'{v:.16f}' for v in row) for row in atoms.cell.array]
    lines += [' '.join(order), ' '.join(str(labels.count(label)) for label in order), 'Selective dynamics', 'Direct']
    for position, mask in zip(atoms.get_scaled_positions(wrap=False), atoms.arrays['vasp_free']):
        lines.append(' '.join(f'{x:.16f}' for x in position) + ' ' + ' '.join('T' if x else 'F' for x in mask))
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return atoms


def prepare(config, config_root, output):
    config_root = Path(config_root).resolve()
    if config.get('source_class') not in {'engineering_fixture', 'user_candidate'} or not isinstance(config.get('provenance'), str) or not config['provenance'].strip():
        raise ValueError('Explicit candidate purpose and scientific/source provenance required')
    threshold = number(config, 'minimum_separation_angstrom', positive=True)
    sources = []
    def source(key, index=0):
        name = config.get(key)
        if not isinstance(name, str) or not name:
            raise ValueError('Unresolved source: ' + key)
        atoms, record = load_structure(config_root / name, config.get('source_format'), index)
        sources.append(record)
        return atoms
    operation = config.get('operation')
    details = {}
    if operation == 'bulk':
        atoms = source('source_file').repeat(integers(config.get('repeats'), 3))
    elif operation == 'slab':
        from ase.build import surface
        atoms = source('source_file')
        if not atoms.arrays['vasp_free'].all():
            raise ValueError('Slab cutting requires an unconstrained bulk source; declare new bottom-plane fixing')
        miller = config.get('miller')
        if not isinstance(miller, list) or len(miller) != 3 or any(type(x) is not int for x in miller) or not any(miller):
            raise ValueError('Explicit nonzero integer Miller indices required')
        layers = config.get('layers')
        if type(layers) is not int or layers < 1:
            raise ValueError('Explicit positive layer count required')
        repeat = integers(config.get('in_plane_repeats'), 2)
        atoms = surface(atoms, miller, layers, periodic=False).repeat([*repeat, 1])
        lower = number(config, 'vacuum_below_angstrom')
        upper = number(config, 'vacuum_above_angstrom')
        atoms.positions[:, 2] += lower - atoms.positions[:, 2].min()
        height = float(atoms.positions[:, 2].max() + upper)
        if height <= 0:
            raise ValueError('Positive slab cell height required')
        cell = atoms.cell.array.copy(); cell[2] = [0, 0, height]
        atoms.set_cell(cell); atoms.pbc = True
        tolerance = number(config, 'plane_tolerance_angstrom', positive=True)
        frozen = config.get('freeze_bottom_planes')
        if type(frozen) is not int or frozen < 0:
            raise ValueError('Explicit nonnegative freeze_bottom_planes required')
        planes = []
        for z in sorted(atoms.positions[:, 2]):
            if not planes or z - planes[-1] > tolerance:
                planes.append(float(z))
        bottom = config.get('trim_bottom_planes')
        top = config.get('trim_top_planes')
        if type(bottom) is not int or type(top) is not int or min(bottom, top) < 0 or bottom + top >= len(planes):
            raise ValueError('Explicit nonnegative top/bottom plane trimming must leave a nonempty slab')
        if bottom or top:
            last = len(planes) - top - 1
            atoms = atoms[(atoms.positions[:, 2] >= planes[bottom] - tolerance) & (atoms.positions[:, 2] <= planes[last] + tolerance)]
            offset = lower - atoms.positions[:, 2].min()
            atoms.positions[:, 2] += offset
            planes = [z + offset for z in planes[bottom:last + 1]]
            cell = atoms.cell.array.copy(); cell[2] = [0, 0, float(atoms.positions[:, 2].max() + upper)]
            atoms.set_cell(cell)
        if frozen > len(planes):
            raise ValueError('More frozen planes requested than observed')
        atoms.arrays['vasp_free'][:] = True
        if frozen:
            atoms.arrays['vasp_free'][atoms.positions[:, 2] <= planes[frozen - 1] + tolerance] = False
        details = {'observed_z_planes_angstrom': planes, 'miller': miller,
                   'trim_bottom_planes': bottom, 'trim_top_planes': top,
                   'observed_bottom_labels': sorted(set(atoms.arrays['vasp_label'][np.abs(atoms.positions[:, 2] - planes[0]) <= tolerance].tolist())),
                   'observed_top_labels': sorted(set(atoms.arrays['vasp_label'][np.abs(atoms.positions[:, 2] - planes[-1]) <= tolerance].tolist())),
                   'termination': config.get('termination_description')}
        if not isinstance(details['termination'], str) or not details['termination'].strip():
            raise ValueError('Explicit termination description required; no stable termination is selected')
    elif operation == 'interface':
        substrate = source('substrate_file', 0)
        film = source('film_file', 1)
        for part in (substrate, film):
            if not np.allclose(part.cell.array, np.diag(np.diag(part.cell.array)), atol=1e-9, rtol=0) or np.any(np.diag(part.cell.array) <= 0):
                raise ValueError('This interface adapter requires axis-aligned orthogonal z-normal slabs')
        if config.get('lateral_match') != 'strain_film_to_substrate':
            raise ValueError('Explicit supported strain allocation required')
        strain = np.diag(substrate.cell.array)[:2] / np.diag(film.cell.array)[:2] - 1
        cell = film.cell.array.copy(); cell[0, 0] = substrate.cell[0, 0]; cell[1, 1] = substrate.cell[1, 1]
        film.set_cell(cell, scale_atoms=True)
        shift = config.get('film_xy_shift_angstrom')
        if not isinstance(shift, list) or len(shift) != 2 or not np.isfinite(np.asarray(shift, dtype=float)).all():
            raise ValueError('Explicit finite lateral shift required')
        lower = number(config, 'vacuum_below_angstrom')
        upper = number(config, 'vacuum_above_angstrom')
        gap = number(config, 'gap_angstrom', positive=True)
        substrate.positions[:, 2] += lower - substrate.positions[:, 2].min()
        film.positions[:, :2] += shift
        film.positions[:, 2] += substrate.positions[:, 2].max() + gap - film.positions[:, 2].min()
        width = max(len(x) for part in (substrate, film) for x in part.arrays['vasp_label'])
        for part in (substrate, film):
            part.arrays['vasp_label'] = np.asarray(part.arrays['vasp_label'], dtype=f'U{width}')
        atoms = substrate + film
        atoms.set_cell([substrate.cell[0, 0], substrate.cell[1, 1], float(film.positions[:, 2].max() + upper)])
        atoms.pbc = True
        details = {'film_lateral_strain': strain.tolist(), 'gap_angstrom': gap,
                   'method': 'Unrelaxed stacked candidate; no commensurate search or stability judgment'}
    else:
        raise ValueError('Choose supported bulk, slab or interface operation')
    if not np.isfinite(atoms.positions).all() or not np.isfinite(atoms.cell.array).all() or atoms.cell.volume <= 0:
        raise ValueError('Generated geometry is invalid')
    close = neighbor_list('d', atoms, threshold, self_interaction=False)
    if len(close):
        raise ValueError('Candidate has pairs below the explicit minimum-separation threshold')
    # Read-only checks above finish before any destination is published.
    out = fresh(output)
    atoms = export_poscar(atoms, out / 'POSCAR', config['source_class'])
    geometry = inspect_geometry(out / 'POSCAR', out)
    with (out / 'atom_mapping.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream); writer.writerow(['output_index_1based', 'label', 'source_id', 'source_index_1based', 'free_flags'])
        for i, (label, origin, index, mask) in enumerate(zip(atoms.arrays['vasp_label'], atoms.arrays['source_id'], atoms.arrays['source_index_1based'], atoms.arrays['vasp_free']), 1):
            writer.writerow([i, label, origin, index, ''.join('T' if x else 'F' for x in mask)])
    record = {'schema': 'vasp-local-structure-candidate/v1', 'operation': operation,
              'software_versions': {name: importlib.metadata.version(name) for name in ('ase', 'numpy', 'pymatgen')},
              'source_class': config['source_class'], 'provenance': config['provenance'],
              'sources': sources, 'config': config, 'atom_count': len(atoms), 'details': details,
              'geometry': geometry, 'poscar_sha256': digest(out / 'POSCAR'),
              'status': 'UNRELAXED_CANDIDATE', 'scientific_acceptance': 'NOT_GRANTED',
              'jobs_submitted': 0, 'workstation_contacted': False}
    write_json(out / 'candidate.json', record)
    return record


def preview(source, output, title, *, fmt='vasp'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ase.data.colors import jmol_colors
    atoms, identity = load_structure(source, fmt)
    if not str(title).strip():
        raise ValueError('Explicit preview title required')
    out = fresh(output)
    fig = plt.figure(figsize=(13, 6.5), constrained_layout=True)
    ax = fig.add_subplot(121, projection='3d')
    side = fig.add_subplot(122)
    colors = [jmol_colors[n] for n in atoms.numbers]
    labels = atoms.arrays['vasp_label'].tolist()
    masks = [''.join('T' if x else 'F' for x in row) for row in atoms.arrays['vasp_free']]
    for label, mask in dict.fromkeys(zip(labels, masks)):
        indices = [i for i, value in enumerate(zip(labels, masks)) if value == (label, mask)]
        positions = atoms.positions[indices]
        marker = 's' if mask == 'FFF' else 'o' if mask == 'TTT' else 'D'
        selected_colors = [colors[i] for i in indices]
        ax.scatter(*positions.T, c=selected_colors, s=100, edgecolors='#263142', marker=marker, depthshade=False)
        side.scatter(positions[:, 0], positions[:, 2], c=selected_colors, s=100, edgecolors='#263142',
                     marker=marker, label=label + ' ' + mask)
    corners = np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)])
    points = corners @ atoms.cell.array
    for i in range(8):
        for j in range(i + 1, 8):
            if np.sum(corners[i] != corners[j]) == 1:
                line = points[[i, j]]
                ax.plot(*line.T, color='#7b8799', lw=0.7)
                side.plot(line[:, 0], line[:, 2], color='#b6beca', lw=0.7)
    grouped = {}
    for index, position in enumerate(atoms.positions, 1):
        grouped.setdefault(tuple(np.round(position[[0, 2]], 8)), []).append(index)
    if len(atoms) <= 40:
        for position, indices in grouped.items():
            caption = '/'.join(str(i) for i in indices)
            if len(atoms) <= 12 and len(indices) == 1:
                caption += ':' + labels[indices[0] - 1] + ' ' + masks[indices[0] - 1]
            side.annotate(caption, position, xytext=(5, 4), textcoords='offset points', fontsize=7)
    ax.set(xlabel='x (angstrom)', ylabel='y (angstrom)', zlabel='z (angstrom)')
    side.set(xlabel='x (angstrom)', ylabel='z (angstrom)', title='Cell and per-atom VASP T/F flags')
    side.set_aspect('equal', adjustable='datalim'); side.grid(alpha=0.15)
    side.legend(loc='upper right', fontsize=8, framealpha=0.9)
    fig.suptitle(title + '\nGeometry preview / no energy or stability evaluation', fontsize=12)
    fig.savefig(out / 'structure.png', dpi=150); plt.close(fig)
    record = {'schema': 'vasp-local-structure-preview/v1', 'source': identity,
              'software_versions': {name: importlib.metadata.version(name) for name in ('ase', 'matplotlib')},
              'title': title, 'labels': labels, 'free_flags': masks,
              'mask_definition': 'T/F along VASP direct cell directions; no Cartesian force projection',
              'image': str(out / 'structure.png'), 'scientific_acceptance': 'NOT_GRANTED'}
    write_json(out / 'preview.json', record)
    return record


def candidates(config, config_root, output, maximum):
    variants = config.get('variants')
    if type(maximum) is not int or maximum < 1 or not isinstance(variants, list) or not variants or len(variants) > maximum:
        raise ValueError('An explicit finite candidate list within the declared budget is required')
    names = [item.get('id') for item in variants if isinstance(item, dict)]
    if len(names) != len(variants) or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name) for name in names) or len(set(names)) != len(names):
        raise ValueError('Distinct safe candidate identifiers required')
    out = fresh(output)
    result = {'schema': 'vasp-local-candidates/v1', 'status': 'IN_PROGRESS', 'candidates': [],
              'jobs_submitted': 0, 'scientific_acceptance': 'NOT_GRANTED'}
    try:
        for item in variants:
            candidate = prepare(item['structure'], config_root, out / item['id'])
            result['candidates'].append({'id': item['id'], 'record': str(out / item['id'] / 'candidate.json'),
                                         'atom_count': candidate['atom_count']})
        result['status'] = 'UNRELAXED_CANDIDATES_PREPARED'
    except Exception as error:
        result.update(status='FAILED_PARTIAL', failure=str(error))
        raise
    finally:
        write_json(out / 'candidates.json', result)
    return result
