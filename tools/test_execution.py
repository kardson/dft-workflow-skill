import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

import execution
from test_environment_check import runtime_directory

TOOLS = Path(os.environ.get("DFT_TOOL_ROOT", str(Path(__file__).resolve().parent)))
sys.path.insert(0, str(TOOLS))
from progress_evidence import _quick_status_canonical
import job_watch


def fixture(state="RUNNING"):
    terminal = state in execution.TERMINAL
    identity = {"task_id": "synthetic-task", "unit_id": "synthetic-unit", "execution_id": "synthetic-execution",
                "host": {"user": "test", "address": "example.invalid", "port": 22},
                "remote_batch_dir": "/synthetic/batch", "remote_case_dir": "/synthetic/batch/case", "tmux_session": "synthetic-session"}
    snapshot = {"schema": "vasp-quick-status/v1", "identity": identity, "canonical_status": state, "state_consistency": "AGREED",
        "remote_observed_utc": "2026-10-14T00:10:00+00:00", "ssh": {"attempted": True, "exit_code": 0, "timed_out": False},
        "sources": {key: {"identity_binding": {"state": "MATCH"}, "raw_status": state, "started_utc": "2026-10-14T00:00:00+00:00"}
                    for key in ("status_json", "run_timing")},
        "processes": [{"state": "ABSENT" if terminal else "PRESENT"}],
        "tmux": {"state": "ABSENT" if terminal else "PRESENT", "pane_dead": terminal},
        "files": {"OSZICAR": {"stable": True, "read_after": {"mtime_utc": "2026-10-14T00:09:00+00:00"}}},
        "progress": {"oszicar": {"tail_visible_electronic_rows": [{"raw": "DAV: 1"}]}}, "reasons": []}
    if terminal:
        snapshot['sources']['run_timing']['exit_codes']={'exit_code':0}
    return snapshot, identity


def verified(snapshot, identity):
    state, consistency, reasons = _quick_status_canonical(snapshot)
    return {"expected": identity, "state": state, "consistency": consistency, "reasons": reasons}


def authority(identity, checksum):
    return {"schema": "dft-execution-authority/v1", **identity, "manifest_sha256": checksum,
            "allowed_actions": ["QUERY", "RECOVER", "ANALYZE"], "user_confirmation": {"reference": "synthetic-user", "quote": "synthetic bounded permission"}}


class ExecutionTests(unittest.TestCase):
    def test_terminal_timing_without_status_needs_exit_and_stable_identity(self):
        snap,identity=fixture('COMPLETED_PENDING_REVIEW')
        snap['sources']['run_timing'].update(raw_status=None,exit_codes={'exit_code':'0'})
        snap['files']['run_timing.txt']={'stable':True,'truncated_prefix':False,'read_before':{'present':True},'read_error':None}
        self.assertEqual(verified(snap,identity)['state'],'COMPLETED_PENDING_REVIEW')
        self.assertEqual(execution.evaluate(snap,verified(snap,identity),'user')['state'],'COMPLETED_PENDING_REVIEW')
        for mutate in ('exit','identity','unstable','live','explicit_unknown'):
            bad=copy.deepcopy(snap)
            if mutate=='exit':bad['sources']['run_timing']['exit_codes']['exit_code']='1'
            if mutate=='identity':bad['sources']['run_timing']['identity_binding']['state']='MISMATCH'
            if mutate=='unstable':bad['files']['run_timing.txt']['stable']=False
            if mutate=='live':bad['processes'][0]['state']='PRESENT'
            if mutate=='explicit_unknown':bad['sources']['run_timing']['raw_status']='UNKNOWN'
            with self.subTest(mutate=mutate):self.assertEqual(verified(bad,identity)['state'],'UNKNOWN')

    def test_initialization_with_confirmed_absent_timing_is_limited(self):
        snap, identity = fixture()
        snap['sources']['run_timing'] = {'available': False, 'identity_binding': {'state':'MISMATCH'}}
        snap['canonical_status'],snap['state_consistency']='UNKNOWN','UNKNOWN'
        snap['files']['run_timing.txt']={'reason':'missing','read_before':{'present':False},'read_bytes':0,'read_error':None}
        snap['files']['vasp.stdout']=copy.deepcopy(snap['files']['OSZICAR'])
        snap['progress']={'stdout':{'tail_lines':[' entering main loop']}}
        result=execution.evaluate(snap,verified(snap,identity),'startup')
        self.assertEqual(result['startup_result'],'INITIALIZED_PENDING_PROGRESS')
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertTrue(result['end_agent_turn'])
        snap['files']['run_timing.txt']['read_before']['present']=True
        self.assertEqual(execution.evaluate(snap,verified(snap,identity),'startup')['startup_result'],'NOT_APPLICABLE')

    def test_initialization_rejects_stale_output_and_fatal_error(self):
        snap, identity = fixture()
        snap['progress']={'stdout':{'tail_lines':['entering main loop']}}
        snap['files']['vasp.stdout']=copy.deepcopy(snap['files']['OSZICAR'])
        snap['files']['vasp.stdout']['read_after']['mtime_utc']='2026-10-13T00:00:00+00:00'
        self.assertEqual(execution.evaluate(snap,verified(snap,identity),'startup')['startup_result'],'PENDING')
        snap['progress']['stderr']={'tail_lines':['MPI_ABORT']}
        self.assertEqual(execution.evaluate(snap,verified(snap,identity),'startup')['startup_result'],'STARTUP_ERROR_EVIDENCE')

    def test_startup_requires_actual_recent_output_and_ends_turn(self):
        snap, identity = fixture()
        result = execution.evaluate(snap, verified(snap, identity), "startup")
        self.assertEqual(result["startup_result"], "STARTED_WITH_OUTPUT_EVIDENCE")
        self.assertTrue(result["end_agent_turn"])
        snap["progress"] = {}
        self.assertEqual(execution.evaluate(snap, verified(snap, identity), "startup")["startup_result"], "PENDING")

    def test_process_conflict_or_old_output_does_not_prove_startup(self):
        snap, identity = fixture()
        snap["files"]["OSZICAR"]["read_after"]["mtime_utc"] = "2026-10-13T00:00:00+00:00"
        self.assertEqual(execution.evaluate(snap, verified(snap, identity), "startup")["startup_result"], "PENDING")
        snap["processes"][0]["state"] = "ABSENT"
        self.assertEqual(execution.evaluate(snap, verified(snap, identity), "startup")["state"], "UNKNOWN")

    def test_terminal_handoff_requires_existing_exact_authority(self):
        snap, identity = fixture("COMPLETED_PENDING_REVIEW")
        check = verified(snap, identity)
        self.assertEqual(execution.evaluate(snap, check, "user")["next_action"], "REPORT_TERMINAL_AND_AUTHORITY_GAP")
        auth = authority(identity, "a" * 64)
        result = execution.evaluate(snap, check, "user", auth, manifest_sha="a" * 64)
        self.assertEqual(result["next_action"], "HANDOFF_TO_RECOVERY_AND_ANALYSIS")
        self.assertFalse(result["recovery_performed"])
        self.assertEqual(result["scientific_acceptance"], "NOT_EVALUATED")

    def test_query_is_one_call_and_timeout_is_not_retried(self):
        snap, identity = fixture()
        calls = []
        with runtime_directory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text("{}")
            auth = authority(identity, execution.digest(manifest))
            def timeout(argv, **kwargs):
                calls.append(argv)
                self.assertEqual(kwargs["timeout"], 35)
                raise subprocess.TimeoutExpired(argv, 35)
            result = execution.query_once(manifest, TOOLS, sys.executable, auth, identity, "user", runner=timeout)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["canonical_status"], "UNKNOWN")
            self.assertTrue(result["ssh"]["timed_out"])

    def test_no_implicit_scheduled_task_or_unapproved_query(self):
        snap, identity = fixture()
        with runtime_directory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text("{}")
            auth = authority(identity, execution.digest(manifest))
            def forbidden(*args, **kwargs):
                self.fail("No query without explicit scheduled authority")
            with self.assertRaises(PermissionError):
                execution.query_once(manifest, TOOLS, sys.executable, auth, identity, "scheduled", runner=forbidden)
            self.assertFalse(execution.permitted(auth, identity, "QUERY", "user", "wrong-manifest"))

    def test_wrong_identity_and_future_observation_are_unknown(self):
        snap, identity = fixture()
        expected = copy.deepcopy(identity)
        snap["identity"]["execution_id"] = "other-attempt"
        snap["remote_observed_utc"] = "2999-01-01T00:00:00+00:00"
        result = execution.evaluate(snap, verified(snap, expected), "user")
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIn("FUTURE_OBSERVATION", result["blockers"])
        self.assertFalse(result["continuous_monitoring"])

    def test_ionic_progress_is_usable_and_fatal_stdout_is_reported(self):
        snap, identity = fixture()
        snap["progress"]["oszicar"] = {"last_visible_complete_ionic_row": {"raw": "1 F= -1 E0= -1"}}
        self.assertEqual(execution.evaluate(snap, verified(snap, identity), "startup")["startup_result"], "STARTED_WITH_OUTPUT_EVIDENCE")
        snap["progress"]["stdout"] = {"tail_lines": ["MPI_ABORT"]}
        self.assertEqual(execution.evaluate(snap, verified(snap, identity), "startup")["startup_result"], "STARTUP_ERROR_EVIDENCE")

    def test_static_startup_without_timing_is_limited_not_fabricated_state(self):
        snap, identity = fixture()
        snap["sources"]["run_timing"] = {"available": False, "identity_binding": {"state": "UNKNOWN"}}
        snap["canonical_status"], snap["state_consistency"] = "UNKNOWN", "UNKNOWN"
        result = execution.evaluate(snap, verified(snap, identity), "startup")
        self.assertEqual(result["startup_result"], "STARTED_WITH_OUTPUT_EVIDENCE")
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertTrue(result["startup_evidence_only"])
        self.assertTrue(result["end_agent_turn"])
        snap["sources"]["run_timing"]["identity_binding"]["state"] = "MISMATCH"
        self.assertNotEqual(execution.evaluate(snap, verified(snap, identity), "startup")["startup_result"], "STARTED_WITH_OUTPUT_EVIDENCE")

    def test_actual_workstation_writer_with_plain_python_child_and_exclusive_lock(self):
        with runtime_directory() as directory:
            root = Path(directory)
            body = "from pathlib import Path;p=Path('case');p.mkdir();" + ";".join(
                "(p/" + repr(name) + ").write_text(" + repr(value) + ")" for name, value in {
                    "OUTCAR": "General timing and accounting informations\naborting loop because EDIFF is reached\n",
                    "OSZICAR": "SYNTHETIC OUTPUT ONLY\n", "CONTCAR": "SYNTHETIC STRUCTURE ONLY\n",
                    "vasprun.xml": "<modeling/>\n", "run_timing.txt": "exit_code=0\n"}.items())
            config = {"task_id": "synthetic-task", "unit_id": "synthetic-unit", "execution_id": "synthetic-execution",
                      "command": [sys.executable, "-B", "-c", body], "cases": [{"path": "case", "kind": "static"}], "notify_argv": []}
            path = root / "watch.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            states = []
            original_save = job_watch.save
            def capture(target, value, **kwargs):
                original_save(target, value, **kwargs)
                states.append(json.loads(Path(target).read_text(encoding="utf-8"))["status"])
            with patch.object(job_watch, "save", side_effect=capture), redirect_stdout(StringIO()):
                self.assertEqual(job_watch.supervise(path, require_tmux=False), 0)
            self.assertIn("STARTING", states)
            self.assertIn("RUNNING", states)
            self.assertIn("COMPLETED_PENDING_REVIEW", states)
            final = json.loads((root / ".job_watch/status.json").read_text(encoding="utf-8"))
            self.assertEqual(final["execution_id"], config["execution_id"])
            self.assertEqual(final["notification"]["status"], "FILE_ONLY")
            with patch.object(job_watch.subprocess, "Popen", side_effect=AssertionError("must not relaunch")):
                with self.assertRaises(FileExistsError):
                    job_watch.supervise(path, require_tmux=False)


if __name__ == "__main__":
    unittest.main()
