"""Explicit quantity metadata comparison and stress conversion; no acceptance."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

FIELDS = {
    'energy': ('unit', 'backend', 'functional', 'paw_environment', 'energy_basis', 'reference_state', 'normalization'),
    'force': ('unit', 'coordinate_basis', 'constraint_basis', 'atom_mapping', 'constraints', 'cell_basis'),
    'potential': ('unit', 'axis', 'potential_component', 'reference_zero', 'sampling_window'),
    'surface': ('unit', 'energy_basis', 'reference_state', 'area_unit', 'surface_count', 'coverage', 'termination'),
    'stress': ('unit', 'sign_convention', 'component_order', 'coordinate_basis'),
}
UNITS = {'energy': {'eV', 'eV/atom'}, 'force': {'eV/angstrom'}, 'potential': {'eV'},
         'surface': {'eV/angstrom^2', 'J/m^2'}, 'stress': {'GPa', 'kbar', 'eV/angstrom^3'}}
COMPONENTS = {'xx', 'yy', 'zz', 'yz', 'xz', 'xy'}


def _known(value):
    if value is None or isinstance(value, str) and (not value.strip() or value in {'UNKNOWN', 'UNRESOLVED', 'NOT_EVALUATED'}):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if isinstance(value, (list, dict)):
        values = value.values() if isinstance(value, dict) else value
        return bool(value) and all(_known(item) for item in values)
    return True


def compare(quantity, left, right):
    if quantity not in FIELDS or not isinstance(left, dict) or not isinstance(right, dict):
        raise ValueError('Explicit supported quantity and two metadata objects required')
    missing, different = [], []
    if left.get('applicable') is False and right.get('applicable') is False and all(_known(item.get('not_applicable_reason')) for item in (left, right)):
        state = 'NOT_APPLICABLE'
    else:
        if (left.get('applicable') is False) != (right.get('applicable') is False):
            different.append('applicability')
        if any(item.get('applicable') is False and not _known(item.get('not_applicable_reason')) for item in (left, right)):
            missing.append('not_applicable_reason')
        for field in FIELDS[quantity]:
            a, b = left.get(field), right.get(field)
            if not _known(a) or not _known(b): missing.append(field)
            elif type(a) is not type(b) or a != b: different.append(field)
        if quantity == 'potential' and any(item.get('reference_zero') == 'SOURCE_LOCPOT_UNSHIFTED' for item in (left, right)):
            missing.append('cross_run_reference_alignment')
        for label, item in (('left', left), ('right', right)):
            structured = {'atom_mapping', 'constraints', 'cell_basis', 'component_order', 'sampling_window', 'surface_count', 'coverage'}
            for field in FIELDS[quantity]:
                if field not in structured and _known(item.get(field)) and not isinstance(item[field], str):
                    different.append(label + ':INVALID_TYPE:' + field)
            if _known(item.get('unit')) and (not isinstance(item['unit'], str) or item['unit'] not in UNITS[quantity]):
                different.append(label + ':UNSUPPORTED_UNIT')
            if quantity == 'energy' and _known(item.get('normalization')):
                expected = {'total': 'eV', 'per_atom': 'eV/atom'}.get(item['normalization']) if isinstance(item['normalization'], str) else None
                if expected is None or _known(item.get('unit')) and item['unit'] != expected:
                    different.append(label + ':ENERGY_NORMALIZATION')
            if quantity == 'potential':
                window = item.get('sampling_window')
                if _known(window) and (not isinstance(window, list) or len(window) != 2 or any(type(x) not in (int, float) for x in window) or window[0] >= window[1]):
                    different.append(label + ':INVALID_WINDOW')
                if _known(item.get('axis')) and item['axis'] not in ('x', 'y', 'z'):
                    different.append(label + ':INVALID_AXIS')
            if quantity == 'stress':
                order = item.get('component_order')
                if _known(order) and (not isinstance(order, list) or len(order) != 6 or any(not isinstance(x, str) for x in order) or set(order) != COMPONENTS):
                    different.append(label + ':INVALID_COMPONENT_ORDER')
                if _known(item.get('sign_convention')) and item['sign_convention'] not in ('compression_positive', 'tension_positive'):
                    different.append(label + ':INVALID_SIGN_CONVENTION')
            if quantity == 'force' and all(_known(item.get(key)) for key in ('atom_mapping', 'constraints', 'cell_basis')):
                mapping, masks, cell = (item[key] for key in ('atom_mapping', 'constraints', 'cell_basis'))
                if (not isinstance(mapping, list) or any(type(x) not in (str, int) for x in mapping)
                        or len({(type(x).__name__, str(x)) for x in mapping}) != len(mapping)
                        or not isinstance(masks, list) or len(mapping) != len(masks)
                        or any(not isinstance(row, list) or len(row) != 3 or any(type(x) is not bool for x in row) for row in masks)
                        or not isinstance(cell, list) or len(cell) != 3
                        or any(not isinstance(row, list) or len(row) != 3 or any(type(x) not in (int, float) for x in row) for row in cell)):
                    different.append(label + ':INVALID_FORCE_MAPPING')
            if quantity == 'surface' and _known(item.get('surface_count')) and (type(item['surface_count']) is not int or item['surface_count'] < 1):
                different.append(label + ':INVALID_SURFACE_COUNT')
        state = 'INCOMPATIBLE' if different else 'INSUFFICIENT_EVIDENCE' if missing else 'MATCH'
    return {'schema': 'dft-quantity-compatibility/v1', 'quantity': quantity, 'state': state,
            'missing_fields': missing, 'different_fields': different, 'left_metadata': left, 'right_metadata': right,
            'scientific_acceptance': 'NOT_GRANTED', 'scope': 'Explicit metadata only; source truth and applicability require route review'}


def convert_stress(value):
    """Convert explicit Voigt units/order/sign without choosing physical settings."""
    from ase import units
    from importlib.metadata import version
    values, order = value.get('values'), value.get('component_order')
    unit, sign = value.get('unit'), value.get('sign_convention')
    if (not isinstance(values, list) or len(values) != 6 or any(type(x) not in (int, float) or not math.isfinite(x) for x in values)
            or not isinstance(order, list) or len(order) != 6 or any(not isinstance(x, str) for x in order) or set(order) != COMPONENTS
            or not isinstance(unit, str) or unit not in UNITS['stress'] or sign not in ('compression_positive', 'tension_positive')
            or value.get('coordinate_basis') != 'Cartesian'):
        raise ValueError('Finite six-component stress with explicit unit, order, sign and Cartesian basis required')
    factor = {'eV/angstrom^3': 1.0, 'GPa': units.GPa, 'kbar': 0.1 * units.GPa}[unit]
    multiplier = factor * (-1 if sign == 'compression_positive' else 1)
    target_order = ['xx', 'yy', 'zz', 'yz', 'xz', 'xy']
    converted = [values[order.index(component)] * multiplier for component in target_order]
    return {'schema': 'dft-stress-conversion/v1', 'original': value, 'converted': {
        'values': converted, 'unit': 'eV/angstrom^3', 'sign_convention': 'tension_positive',
        'component_order': target_order, 'coordinate_basis': 'Cartesian'},
        'conversion': {'multiplier': multiplier, 'ase_version': version('ase'), 'unit_factor_source': 'ase.units.GPa'},
        'scientific_acceptance': 'NOT_GRANTED'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists(): raise ValueError('Preserve existing output')
    request = json.loads(args.request.read_text(encoding='utf-8-sig'))
    if request.get('schema') != 'dft-quantity-request/v1': raise ValueError('Unsupported request schema')
    if request.get('operation') == 'compare':
        result = compare(request.get('quantity'), request.get('left'), request.get('right'))
    elif request.get('operation') == 'convert_stress': result = convert_stress(request['stress'])
    else: raise ValueError('Explicit compare or convert_stress operation required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream: json.dump(result, stream, ensure_ascii=False, indent=2)
    return 1 if result.get('state') in {'INCOMPATIBLE', 'INSUFFICIENT_EVIDENCE'} else 0


if __name__ == '__main__': raise SystemExit(main())
