import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import execution
import recovery
from test_environment_check import runtime_directory
from test_execution import authority

TOOLS = Path(os.environ.get("DFT_TOOL_ROOT", str(Path(__file__).resolve().parent)))
sys.path.insert(0, str(TOOLS))
from progress_evidence import _load_raw_evidence_identity


def fixture(root, partial=False):
    manifest = {"route": "vasp", "task_id": "synthetic-recovery", "unit_id": "synthetic-unit", "execution_id": "synthetic-execution",
                "host": {"user": "vasp", "address": "example.invalid", "port": 22},
                "remote_batch_dir": "/srv/dft/calculations/synthetic-recovery", "case": "case",
                "runtime_input_dir": "/srv/dft/calculations/synthetic-recovery/case"}
    path = root / "manifest.json"
    manifest['environment']={'vasp_version':'6.5.1'}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    expected = _load_raw_evidence_identity(path)
    status = {key: manifest[key] for key in ("task_id", "unit_id", "execution_id")}
    status["status"] = "FAILED_OR_INCOMPLETE" if partial else "COMPLETED_PENDING_REVIEW"
    data = {name: ("SYNTHETIC " + name + "\n").encode() for name in recovery.REQUIRED}
    data["status.json"] = json.dumps(status).encode()
    data["POSCAR"] = b"Synthetic Si\n1\n1 0 0\n0 1 0\n0 0 1\nSi\n1\nDirect\n0 0 0\n"
    data["INCAR"] = b"NSW=0\nENCUT=300\n"
    data["KPOINTS"] = b"Synthetic\n0\nGamma\n1 1 1\n0 0 0\n"
    if partial:
        data.pop("vasprun.xml")
    members = [{"name": name, "source_path": recovery.source_path(expected, name), "content_b64": base64.b64encode(content).decode(),
                "sha256": hashlib.sha256(content).hexdigest(), "is_symlink": False,
                "read_before": {"present": True, "bytes": len(content), "mtime_utc": "2026-10-14T00:00:00+00:00"},
                "read_after": {"present": True, "bytes": len(content), "mtime_utc": "2026-10-14T00:00:00+00:00"}}
               for name, content in data.items()]
    payload = {"schema": "dft-terminal-export/v1", "manifest_identity": expected, "members": members,
               "observed_start_utc": "2026-10-14T00:00:00+00:00", "observed_end_utc": "2026-10-14T00:01:00+00:00"}
    (root / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    (root / "authority.json").write_text(json.dumps(authority(expected, execution.digest(path))), encoding="utf-8")
    handoff = {"schema": "dft-execution-handoff/v1", "identity": expected, "state": status["status"],
               "next_action": "HANDOFF_TO_RECOVERY_AND_ANALYSIS", "trigger": "user", "blockers": []}
    (root / "handoff.json").write_text(json.dumps(handoff), encoding="utf-8")
    return expected, payload


class RecoveryTests(unittest.TestCase):
    def test_complete_terminal_outcar_is_explicit_and_bound(self):
        from progress_evidence import ingest_export, _verify_record
        from vasp_operations import _frames_for_payload
        with runtime_directory() as directory:
            root=Path(directory);identity,payload=fixture(root)
            spec=root/'input_manifest.json'
            spec.write_text(json.dumps(dict(task_id=identity['task_id'],unit_id=identity['unit_id'],structure={'nions':1,'nelect':4})),encoding='utf-8')
            prep=root/'preparation.json'
            prep.write_text(json.dumps(dict(status='LOCAL_PACKAGE_CHECKED',blockers=[],task_id=identity['task_id'],candidate=str(root),candidate_manifest_sha256=execution.digest(spec),candidate_execution_manifest_sha256=execution.digest(root/'manifest.json'))),encoding='utf-8')
            text='vasp.6.5.1 SYNTHETIC\nINCAR:\nENMAX = 1 SYNTHETIC METADATA\nDimension of arrays\nNIONS = 1\nNELECT = 4\nPOSITION TOTAL-FORCE\nLOOP: 0.1\nGeneral timing and accounting informations\n'
            def frames(content):
                members=[]
                for row in payload['members']:
                    data=content.encode() if row['name']=='OUTCAR' else base64.b64decode(row['content_b64'])
                    stat={**row['read_before'],'bytes':len(data)}
                    members.append({**row,'content':data,'read_before':stat,'read_after':stat})
                return _frames_for_payload(identity,members,{row['name']:row for row in members},payload['observed_start_utc'],payload['observed_end_utc'])
            def ingest(name,content=text,**kwargs):
                return ingest_export(frames(content),root,root/'manifest.json',snapshot_name=name,command_exit_code=0,**kwargs)
            default=ingest('strict')
            self.assertEqual(json.loads((default/'evidence.json').read_text())['collection']['status'],'PARTIAL')
            policy=dict(purpose='authorized_terminal_full_outputs',preparation_path=prep)
            bundle=ingest('full',**policy);doc=json.loads((bundle/'evidence.json').read_text())
            self.assertEqual(doc['collection']['status'],'COMPLETE',doc['collection'])
            record=doc['files']['OUTCAR'][0];issues=[];gaps=[]
            self.assertEqual(_verify_record(bundle,identity,'OUTCAR',record,issues,gaps),text)
            self.assertFalse(issues,issues)
            early=json.loads(json.dumps(record));early['terminal_full_output_context'].pop('vasp_version')
            early_issues=[]
            self.assertEqual(_verify_record(bundle,identity,'OUTCAR',early,early_issues,[]),text)
            self.assertFalse(early_issues)
            conflict=json.loads(json.dumps(record));conflict['terminal_full_output_context']['vasp_version']='0.0.0'
            conflict_issues=[];_verify_record(bundle,identity,'OUTCAR',conflict,conflict_issues,[])
            self.assertTrue(conflict_issues)
            for index,body in enumerate(['End of Dataset','PSCTR','radial grid\n1 2 3','Non local Part','pseudo wavefunctions']):
                bad=ingest('body'+str(index),content=text+body+'\n',**policy)
                self.assertEqual(json.loads((bad/'evidence.json').read_text())['collection']['status'],'PARTIAL')
            for index,content in enumerate(['ENMAX = 1\n',text.replace('NIONS = 1','NIONS = 2'),text.replace('NELECT = 4','NELECT = 5')]):
                bad=ingest('shape'+str(index),content=content,**policy)
                self.assertEqual(json.loads((bad/'evidence.json').read_text())['collection']['status'],'PARTIAL')
            with self.assertRaises(ValueError):ingest('wrongpurpose',purpose='fake',preparation_path=prep)
            for change in ('source','truncated','unstable','execution'):
                changed=[json.loads(row) for row in frames(text)]
                out=next(row for row in changed if row.get('role')=='OUTCAR')
                if change=='source':out['source_path']='/other/OUTCAR'
                if change=='truncated':out['coverage']='tail'
                if change=='unstable':out['read_after']['bytes']+=1
                if change=='execution':
                    status=next(row for row in changed if row.get('role')=='job_status')
                    content=json.loads(base64.b64decode(status['content_b64']));content['execution_id']='wrong'
                    status['content_b64']=base64.b64encode(json.dumps(content).encode()).decode()
                if change=='execution':
                    with self.assertRaises(ValueError):ingest_export([json.dumps(row) for row in changed],root,root/'manifest.json',snapshot_name=change,**policy)
                else:
                    rejected=ingest_export([json.dumps(row) for row in changed],root,root/'manifest.json',snapshot_name=change,**policy)
                    self.assertEqual(json.loads((rejected/'evidence.json').read_text())['collection']['status'],'PARTIAL')
            for row in payload['members']:
                if row['name']=='OUTCAR':
                    raw=text.encode();row.update(content_b64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
                    row['read_before']['bytes']=len(raw);row['read_after']['bytes']=len(raw)
            (root/'payload.json').write_text(json.dumps(payload),encoding='utf-8')
            argv=['ingest','--full-outputs','--preparation',str(prep),'--max-total-bytes','100000','--transport-exit-code','0']
            for name,value in {'manifest':root/'manifest.json','handoff':root/'handoff.json','authority':root/'authority.json','payload':root/'payload.json','tool-root':TOOLS,'output-dir':root/'full-cli'}.items():argv+=['--'+name,str(value)]
            self.assertEqual(recovery.main(argv),0)
            self.assertEqual((root/'full-cli/analysis_source/OUTCAR').read_bytes(),text.encode())
            (bundle/record['snapshot_path']).write_bytes((text+'tampered').encode())
            issues=[];_verify_record(bundle,identity,'OUTCAR',record,issues,[])
            self.assertTrue(issues)
            prepared=json.loads(prep.read_text());prepared['candidate_execution_manifest_sha256']='0'*64;prep.write_text(json.dumps(prepared))
            with self.assertRaises(ValueError):ingest('wrongprep',**policy)

    def test_payload_identity_digest_budget_and_whitelist(self):
        with runtime_directory() as directory:
            root = Path(directory)
            expected, payload = fixture(root)
            rows, state, missing = recovery.validate(payload, expected, 100000)
            self.assertFalse(missing)
            self.assertEqual(rows["POSCAR"][:12], b"Synthetic Si")
            payload["members"][0]["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                recovery.validate(payload, expected, 100000)
            payload["members"][0]["name"] = "POTCAR"
            with self.assertRaises(ValueError):
                recovery.validate(payload, expected, 100000)

    def test_partial_failed_export_stays_partial(self):
        with runtime_directory() as directory:
            expected, payload = fixture(Path(directory), partial=True)
            self.assertIn("vasprun.xml", recovery.validate(payload, expected, 100000)[2])
            with self.assertRaises(ValueError):
                recovery.validate(payload, expected, 2)

    def test_actual_canonical_import_and_analysis_view(self):
        for partial in (False, True):
            with self.subTest(partial=partial), runtime_directory() as directory:
                root = Path(directory)
                fixture(root, partial=partial)
                argv = ["ingest", "--max-total-bytes", "100000", "--transport-exit-code", "0"]
                for name, value in {"manifest": root / "manifest.json", "handoff": root / "handoff.json", "authority": root / "authority.json",
                                    "payload": root / "payload.json", "tool-root": TOOLS, "output-dir": root / "returned"}.items():
                    argv += ["--" + name, str(value)]
                self.assertEqual(recovery.main(argv), 0)
                report = json.loads((root / "returned/recovery_handoff.json").read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "RECOVERED_PARTIAL" if partial else "RECOVERED_COMPLETE")
                self.assertTrue((Path(report["raw_bundle"]) / "evidence.json").is_file())
                self.assertEqual(report["scientific_acceptance"], "NOT_EVALUATED")
                self.assertFalse(report["remote_calls_by_this_tool"])
                self.assertTrue((root / "returned/analysis_source/POSCAR").is_file())

    def test_actual_exporter_reads_only_fixed_files_and_enforces_budget(self):
        with runtime_directory() as directory:
            root = Path(directory)
            expected, payload = fixture(root)
            station = root / "station"
            case = station / "case"
            case.mkdir(parents=True)
            (station / ".job_watch").mkdir()
            expected.update(remote_batch_dir=str(station.resolve()), remote_case_dir=str(case.resolve()), remote_input_dir=str(case.resolve()))
            for row in payload["members"]:
                target = station / ".job_watch/status.json" if row["name"] == "status.json" else case / row["name"]
                target.write_bytes(base64.b64decode(row["content_b64"]))
            (case / "POTCAR").write_text("SYNTHETIC FORBIDDEN SENTINEL")
            script = root / "export.py"
            script.write_text(recovery.export_script(expected, 100000), encoding="utf-8")
            child = subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            result = json.loads(child.stdout)
            self.assertNotIn("POTCAR", {row["name"] for row in result["members"]})
            self.assertTrue(all(hashlib.sha256(base64.b64decode(row["content_b64"])).hexdigest() == row["sha256"] for row in result["members"]))
            script.write_text(recovery.export_script(expected, 1), encoding="utf-8")
            child = subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(child.returncode, 0)

    def test_active_handoff_cannot_generate_export_script(self):
        with runtime_directory() as directory:
            root = Path(directory)
            fixture(root)
            path = root / "handoff.json"
            handoff = json.loads(path.read_text())
            handoff["state"] = "RUNNING"
            path.write_text(json.dumps(handoff))
            args = ["script", "--max-total-bytes", "100000"]
            for name, value in {"manifest": root / "manifest.json", "handoff": path, "authority": root / "authority.json", "tool-root": TOOLS, "output-dir": root / "script"}.items():
                args += ["--" + name, str(value)]
            with self.assertRaises(PermissionError):
                recovery.main(args)
            self.assertFalse((root / "script").exists())


if __name__ == "__main__":
    unittest.main()
