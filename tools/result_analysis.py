"""Offline result parsing; no calculation or scientific acceptance."""
import csv
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pymatgen.io.vasp.outputs import Vasprun, Oszicar, Outcar
from practical_geometry import inspect_geometry, export_frame

def destination(path):
    path = Path(path).resolve()
    if path.exists():
        raise ValueError('Output must be a new directory')
    path.mkdir(parents=True)
    return path

def analyze(source, output):
    source = Path(source).resolve()
    output = Path(output).resolve()
    if output == source or source in output.parents:
        raise ValueError('Keep analysis outputs outside the original calculation package')
    out = destination(output)
    report = dict(schema='vasp-practical-analysis/v1', source=str(source),
                  scientific_acceptance='NOT_EVALUATED', parser='pymatgen', status='PARTIAL')
    steps = []
    try:
        run = Vasprun(source/'vasprun.xml', parse_dos=False, parse_eigen=False, parse_potcar_file=False,
                      exception_on_bad_xml=True)
        # Read masks from POSCAR through the established route parser, which preserves pseudo-H labels.
        from progress_snapshot import parse_poscar
        poscar = parse_poscar((source/'POSCAR').read_text(), source/'POSCAR')
        if not poscar['valid']:
            raise ValueError('Actual POSCAR invalid')
        masks = [atom['flags'] for atom in poscar['atoms']]
        if poscar['nions'] != len(run.final_structure):
            raise ValueError('POSCAR/XML atom counts differ')
        geometry=inspect_geometry(source/'POSCAR')
        if list(run.atomic_symbols)!=geometry['chemical_symbols']:
            raise ValueError('POSCAR/XML species order differs')
        report['geometry_inspection']=geometry
        report.update(status='COMPLETE_XML', electronic_converged=bool(run.converged_electronic),
                      ionic_converged=bool(run.converged_ionic), nions=len(run.final_structure),
                      efermi_eV=run.efermi, parameters={k:run.parameters.get(k) for k in ['NSW','NELM','EDIFF','EDIFFG','IBRION','ISTART','ICHARG']},
                      actual_input_structure=poscar)
        for index, step in enumerate(run.ionic_steps, 1):
            forces = np.asarray(step.get('forces', []))
            free_force = None
            if forces.size and poscar['mask_status'] == 'OK':
                masked = np.where(np.asarray(masks, dtype=bool), forces, 0)
                free_force = float(np.linalg.norm(masked, axis=1).max())
            steps.append(dict(step=index, free_energy_eV=step.get('e_fr_energy'),
                              energy_zero_eV=step.get('e_0_energy'), electronic_steps=len(step.get('electronic_steps',[])),
                              max_force_eV_A=float(np.linalg.norm(forces,axis=1).max()) if forces.size else None,
                              max_free_force_eV_A=free_force))
        report['final_structure'] = run.final_structure.as_dict()
        export_frame(run.final_structure,[atom['species'] for atom in poscar['atoms']],masks,out/'final_geometry.extxyz')
        if (source/'OUTCAR').is_file():
            try:
                outcar = Outcar(source/'OUTCAR')
                report.update(efermi_eV=outcar.efermi, nelect=outcar.nelect,
                              total_magnetization=outcar.total_mag,
                              magnetization=list(outcar.magnetization))
            except Exception as error:
                report['outcar_error'] = f'{type(error).__name__}: {error}'
    except Exception as error:
        report['status'] = 'PARTIAL'
        for key in ('electronic_converged','ionic_converged','final_structure'):
            report.pop(key,None)
        steps = []
        report['parse_error'] = f'{type(error).__name__}: {error}'
        if (source/'OSZICAR').is_file():
            try:
                for index, step in enumerate(Oszicar(source/'OSZICAR').ionic_steps, 1):
                    steps.append(dict(step=index, free_energy_eV=step.get('F'), energy_zero_eV=step.get('E0')))
            except Exception as oszicar_error:
                report['oszicar_error']=f'{type(oszicar_error).__name__}: {oszicar_error}'
        # A truncated XML never becomes a complete/converged result via fallback.
    report['steps'] = steps
    (out/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if steps:
        fields = list(dict.fromkeys(key for row in steps for key in row))
        with (out/'ionic_steps.csv').open('w', newline='', encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(steps)
        fig, ax = plt.subplots(figsize=(6,4))
        ax.plot([x['step'] for x in steps],[x['free_energy_eV'] for x in steps],marker='o')
        ax.set(xlabel='Ionic step',ylabel='Free energy (eV)',title=report['status'])
        fig.tight_layout();fig.savefig(out/'energy.png',dpi=150);plt.close(fig)
        if any(x.get('max_free_force_eV_A') is not None for x in steps):
            fig, ax=plt.subplots(figsize=(6,4))
            ax.plot([x['step'] for x in steps],[x.get('max_free_force_eV_A') for x in steps],marker='o')
            ax.set(xlabel='Ionic step',ylabel='Maximum force on free atoms (eV/angstrom)')
            fig.tight_layout();fig.savefig(out/'free_force.png',dpi=150);plt.close(fig)
    return report
