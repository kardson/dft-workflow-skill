import copy
import json
import os
from pathlib import Path
import sys
import unittest

import preparation
from test_environment_check import runtime_directory
from test_planning import fixture as plan_fixture

TOOLS = Path(os.environ.get("DFT_TOOL_ROOT", str(Path(__file__).resolve().parent)))
sys.path.insert(0, str(TOOLS))
import test_static_delivery_builder as fixtures
import static_delivery_builder as builder
import practical_execution_builder as execution_builder
import vasp_input_generator as generator
import planning


def fixture(root):
    source = root / "source"
    source.mkdir()
    fixtures._source_files(source)
    manifest = fixtures._manifest(source)
    spec = root / "approved.json"
    spec.write_text(json.dumps(manifest), encoding="utf-8")
    candidate = root / "candidate"
    builder.build_static_delivery(spec, source, candidate)
    plan = plan_fixture(root)
    plan["task_id"] = manifest["task_id"]
    intake = json.loads((root / "intake.json").read_text())
    intake["task_id"] = plan["task_id"]
    (root / "intake.json").write_text(json.dumps(intake))
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    approval = {"schema": "dft-plan-approval/v1", "task_id": plan["task_id"], "stage_id": "static1",
                "plan_sha256": preparation.digest(plan_path), "approved_manifest_sha256": preparation.digest(spec),
                "user_confirmation": {"reference": "synthetic-user-message", "quote": "synthetic approval only"},
                "scientific_decision_ref": "method.txt"}
    approval_path = root / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    return candidate, plan_path, approval_path, spec


def fixed_fixture(root, warm=False):
    source = root / "source"
    source.mkdir()
    fixtures._source_files(source)
    manifest = fixtures._manifest(source)
    manifest.pop("delivery_identity")
    manifest["environment_id"] = "synthetic-runtime"
    manifest["incar"].update(IBRION=2, NSW=4, EDIFFG_eV_per_A=-0.02)
    manifest["restart"]["mode"] = "fresh"
    roles = manifest["structure"]["paw_order"]
    manifest["paw_identity"] = {"environment_id": manifest["environment_id"], "paw_family": "PBE.64",
                                "ordered_roles": roles, "ordered_poscar_labels": manifest["structure"]["species_order"],
                                "combined_potcar_sha256": "c" * 64,
                                "potcar_content_read": False, "potcar_created": False,
                                **{role: {"ZVAL": zval, "component_sha256": token * 64} for role, zval, token in zip(roles, [8., 10.], "ab")}}
    manifest["environment"].update(environment_id=manifest["environment_id"], paw_family="PBE.64", omp_num_threads=1,
        mpi_args=["--map-by", "core", "--bind-to", "core", "--nooversubscribe", "--report-bindings"])
    manifest["source"] = {"parent_repo_path": (source / "POSCAR").relative_to(Path(os.environ.get("DFT_WORKSPACE_ROOT", str(TOOLS.parent))).resolve()).as_posix(),
                          "parent_sha256": preparation.digest(source / "POSCAR"), "source_kind": "synthetic/POSCAR"}
    if warm:
        manifest["incar"].update(ISTART=1, ICHARG=1)
        manifest["outputs"] = {"POSCAR": {"sha256": preparation.digest(source / "POSCAR"), "size_bytes": (source / "POSCAR").stat().st_size}}
        parent = "/synthetic/accepted/case"
        manifest["restart"] = {"mode": "wavefunction_and_charge_scf", "ISTART": 1, "ICHARG": 1, "fresh": False,
            "source": {"unit_id": "synthetic-parent", "task_id": root.name, "case_id": "case", "case_path": parent,
                "poscar_sha256": preparation.digest(source / "POSCAR"), "environment_id": manifest["environment_id"],
                "paw_family": "PBE.64", "ordered_paw_roles": roles, "combined_potcar_sha256": "c" * 64,
                "encut_eV": manifest["incar"]["ENCUT_eV"], "kpoints": manifest["kpoints"], "nbands": manifest["incar"]["NBANDS"],
                "ispin": manifest["incar"]["ISPIN"], "magmom": manifest["incar"]["MAGMOM"], "nupdown": manifest["incar"]["NUPDOWN"],
                "compatibility": {key: "MATCH" for key in ("geometry", "environment", "paw", "encut", "kpoints", "nbands", "spin")},
                "files": {name: {"source_path": parent + "/" + name, "source_postcheck_nonempty": True,
                    "source_size_bytes": 1024, "source_observed_utc": "2026-10-14T00:00:00+00:00", "local_state": "NOT_DOWNLOADED",
                    "target_preflight_state": "REMOTE_RESTART_PENDING"} for name in ("WAVECAR", "CHGCAR")},
                "copy_policy": "REGULAR_FILE_COPY_NO_HARDLINK", "remote_preflight_state": "REMOTE_RESTART_PENDING"}}
    spec = root / "approved.json"
    spec.write_text(json.dumps(manifest), encoding="utf-8")
    candidate = root / "candidate"
    generator.generate_candidate(spec, source, candidate)
    execution_source = {key: manifest[key] for key in ("task_id", "unit_id", "host", "remote_batch_dir", "case", "case_dir", "runtime_input_dir", "tmux_session", "environment_id")}
    requirements = builder._output_requirements(manifest)
    requirements["kind"] = "relaxation"
    requirements.pop("static_policy")
    requirements["markers"].append({"file": "OUTCAR", "name": "ionic", "pattern": "reached required accuracy", "category": "ionic"})
    for name, value in (("execution.json", execution_source), ("requirements.json", requirements), ("environment.json", manifest["environment"])):
        (root / name).write_text(json.dumps(value), encoding="utf-8")
    execution_builder.build(candidate, root / "execution.json", root / "requirements.json", root / "environment.json")
    contract = root / "runtime_contract.json"
    contract.write_text(json.dumps({"execution_source": execution_source, "requirements_source": requirements, "environment": manifest["environment"]}), encoding="utf-8")
    plan = plan_fixture(root)
    plan["task_id"] = manifest["task_id"]
    intake = json.loads((root / "intake.json").read_text())
    intake["task_id"] = plan["task_id"]
    (root / "intake.json").write_text(json.dumps(intake))
    stage = plan["stages"][0]
    stage["template"] = "relax_warm" if warm else "relax_fresh"
    template = planning.catalog()[stage["template"]]
    stage["choices"] = {key: "explicit synthetic choice" for key in template["required_choices"]}
    stage["acceptance_checks"] = {key: "explicit synthetic check" for key in template["required_checks"]}
    stage["deliverables"] = template["expected_outputs"]
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    approval = {"schema": "dft-plan-approval/v1", "task_id": plan["task_id"], "stage_id": stage["id"],
        "plan_sha256": preparation.digest(plan_path), "approved_manifest_sha256": preparation.digest(spec),
        "user_confirmation": {"reference": "synthetic-user", "quote": "synthetic approval"}, "scientific_decision_ref": "method.txt",
        "runtime_contract_ref": contract.name, "runtime_contract_sha256": preparation.digest(contract)}
    approval_path = root / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    return candidate, plan_path, approval_path, spec


class PreparationTests(unittest.TestCase):
    def audit(self, root, paths, **kwargs):
        return preparation.audit(*paths, root, TOOLS, sys.executable, **kwargs)

    def test_real_builder_and_existing_actual_file_check(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            result = self.audit(root, paths)
            self.assertEqual(result["status"], "LOCAL_PACKAGE_CHECKED", result)
            self.assertTrue(result["existing_checks"]["actual_inputs"]["passed"])
            self.assertFalse(result["execution_authorized"])

    def test_approval_is_bound_to_exact_plan_bytes(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            with paths[1].open("a") as handle:
                handle.write("\n")
            self.assertIn("APPROVAL_OR_EXACT_SOURCE_BINDING_MISSING", self.audit(root, paths)["blockers"])

    def test_scientific_change_cannot_hide_in_candidate_manifest(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            target = paths[0] / "input_manifest.json"
            value = json.loads(target.read_text())
            value["incar"]["ENCUT_eV"] += 20
            target.write_text(json.dumps(value))
            result = self.audit(root, paths)
            self.assertIn("SCIENTIFIC_SPECIFICATION_CHANGED", result["blockers"])

    def test_actual_input_tampering_fails_existing_checker(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            path = paths[0] / "INCAR"
            path.write_text(path.read_text().replace("ENCUT = 300", "ENCUT = 999"))
            # The actual parser/identity check must reject a changed real input.
            with self.assertRaises(ValueError):
                self.audit(root, paths)

    def test_sensitive_file_prevents_any_checker_read(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            (paths[0] / "POTCAR").write_text("synthetic sentinel, not licensed data")
            def forbidden(*args):
                self.fail("Checker must not be called for a sensitive-file candidate")
            self.assertIn("SENSITIVE_OR_RESULT_FILES_IN_CANDIDATE", self.audit(root, paths, checker=forbidden)["blockers"])

    def test_missing_runtime_member_is_not_complete(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            # Use a zero-byte runner; no deletion of any source or prior result.
            (paths[0] / "run_static.sh").write_text("")
            with self.assertRaises(ValueError):
                self.audit(root, paths)

    def test_fixed_fresh_and_warm_existing_generation_and_checks(self):
        for warm in (False, True):
            with self.subTest(warm=warm), runtime_directory() as directory:
                root = Path(directory)
                paths = fixed_fixture(root, warm)
                result = self.audit(root, paths)
                self.assertEqual(result["status"], "LOCAL_PACKAGE_CHECKED", result)
                self.assertTrue(result['existing_checks']['delivery']['output_requirements_match'])
                self.assertEqual(result['candidate_output_requirements_sha256'], preparation.digest(paths[0] / 'output_requirements.json'))
                self.assertFalse(result["execution_authorized"])

    def test_fixed_rehashed_output_requirements_must_match_approved_contract(self):
        for warm in (False, True):
            for change in ('required', 'nonempty', 'marker', 'identity', 'extra', 'type'):
                with self.subTest(warm=warm, change=change), runtime_directory() as directory:
                    root = Path(directory)
                    paths = fixed_fixture(root, warm)
                    candidate = paths[0]
                    contract_bytes = (root / 'runtime_contract.json').read_bytes()
                    requirements = preparation.read(candidate / 'output_requirements.json')
                    if change == 'required':
                        requirements['required_files'].remove('vasprun.xml')
                    elif change == 'nonempty':
                        requirements['nonempty_files'].remove('vasprun.xml')
                    elif change == 'marker':
                        requirements['markers'][0]['required'] = False
                    elif change == 'identity':
                        requirements['expected_identity']['VASP_VERSION'] = 'changed-version'
                    elif change == 'extra':
                        requirements['unapproved_option'] = True
                    else:
                        requirements['markers'][0]['required'] = 1
                    target = candidate / 'output_requirements.json'
                    target.write_text(json.dumps(requirements), encoding='utf-8')
                    checksums = preparation.read(candidate / 'runtime_checksums.json')
                    checksums[target.name] = preparation.digest(target)
                    (candidate / 'runtime_checksums.json').write_text(json.dumps(checksums), encoding='utf-8')
                    gate = preparation.read(candidate / 'runtime_gate.json')
                    gate['package_checksum_sha256'] = preparation.digest(candidate / 'runtime_checksums.json')
                    (candidate / 'runtime_gate.json').write_text(json.dumps(gate), encoding='utf-8')
                    result = self.audit(root, paths)
                    self.assertEqual(result['status'], 'BLOCKED')
                    self.assertIn('OUTPUT_REQUIREMENTS_CONTRACT_MISMATCH', result['blockers'])
                    self.assertIsNone(result['candidate_output_requirements_sha256'])
                    self.assertEqual((root / 'runtime_contract.json').read_bytes(), contract_bytes)
                    if change == 'required':
                        import analysis
                        from test_analysis import fixture as analysis_fixture
                        context = root / 'analysis-context'
                        context.mkdir()
                        recovered = analysis_fixture(context)
                        numerical = preparation.read(candidate / 'input_manifest.json')
                        recovered['identity'].update({key: numerical[key] for key in ('task_id', 'unit_id')})
                        recovery_path = context / 'recovery.json'
                        recovery_path.write_text(json.dumps(recovered), encoding='utf-8')
                        prepared_path = root / 'blocked-preparation.json'
                        prepared_path.write_text(json.dumps(result), encoding='utf-8')
                        def forbidden_backend(*args, **kwargs):
                            self.fail('Blocked output contract reached the analysis backend')
                        with self.assertRaisesRegex(ValueError, 'Prepared package is not bound'):
                            analysis.parse(recovery_path, paths[1], 'static1', candidate / 'input_manifest.json',
                                target, root, TOOLS, sys.executable, root / 'analysis', runner=forbidden_backend,
                                preparation=prepared_path)
                        self.assertFalse((root / 'analysis').exists())

    def test_fixed_gate_and_rehashed_runner_tampering(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixed_fixture(root)
            candidate = paths[0]
            runner = candidate / "run_task.sh"
            runner.write_text("#!/bin/sh\necho unauthorized replacement\n")
            checksums = preparation.read(candidate / "runtime_checksums.json")
            checksums["run_task.sh"] = preparation.digest(runner)
            (candidate / "runtime_checksums.json").write_text(json.dumps(checksums))
            gate = preparation.read(candidate / "runtime_gate.json")
            gate["package_checksum_sha256"] = preparation.digest(candidate / "runtime_checksums.json")
            (candidate / "runtime_gate.json").write_text(json.dumps(gate))
            self.assertIn("DELIVERY_CHECK_FAILED", self.audit(root, paths)["blockers"])
            gate["launch"] = True
            (candidate / "runtime_gate.json").write_text(json.dumps(gate))
            self.assertIn("RUNTIME_GATE_NOT_CLOSED_OR_WRONG_IDENTITY", self.audit(root, paths)["blockers"])

    def test_cli_missing_package_generates_blocked_handoff(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            (paths[0] / "run_static.sh").write_text("")
            argv = []
            for key, value in {"candidate": paths[0], "plan": paths[1], "approval": paths[2], "approved-manifest": paths[3],
                               "workspace": root, "tool-root": TOOLS, "output-dir": root / "handoff"}.items():
                argv.extend(["--" + key, str(value)])
            self.assertEqual(preparation.main(argv), 1)
            self.assertEqual(preparation.read(root / "handoff/preparation.json")["status"], "BLOCKED")

    def test_new_scientific_key_and_source_frame_change_are_rejected(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixture(root)
            path = paths[0] / "input_manifest.json"
            original = preparation.read(path)
            changed = copy.deepcopy(original)
            changed["incar"]["unapproved_setting"] = True
            path.write_text(json.dumps(changed))
            self.assertIn("SCIENTIFIC_SPECIFICATION_CHANGED", self.audit(root, paths)["blockers"])
            changed = copy.deepcopy(original)
            changed["delivery_identity"]["source"]["calculation_index_1_based"] = 2
            path.write_text(json.dumps(changed))
            self.assertIn("delivery_identity", self.audit(root, paths)["scientific_differences"])

    def test_fixed_watch_rehash_and_contract_change_are_rejected(self):
        with runtime_directory() as directory:
            root = Path(directory)
            paths = fixed_fixture(root, True)
            candidate = paths[0]
            watch = preparation.read(candidate / "watch.json")
            watch["notify_argv"] = ["unexpected-notification"]
            (candidate / "watch.json").write_text(json.dumps(watch))
            checksums = preparation.read(candidate / "runtime_checksums.json")
            checksums["watch.json"] = preparation.digest(candidate / "watch.json")
            (candidate / "runtime_checksums.json").write_text(json.dumps(checksums))
            gate = preparation.read(candidate / "runtime_gate.json")
            gate["package_checksum_sha256"] = preparation.digest(candidate / "runtime_checksums.json")
            (candidate / "runtime_gate.json").write_text(json.dumps(gate))
            self.assertIn("DELIVERY_CHECK_FAILED", self.audit(root, paths)["blockers"])
            with (root / "runtime_contract.json").open("a") as handle:
                handle.write("\n")
            self.assertIn("APPROVED_RUNTIME_CONTRACT_CHANGED", self.audit(root, paths)["blockers"])


if __name__ == "__main__":
    unittest.main()
