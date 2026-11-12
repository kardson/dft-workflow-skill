"""Offline presentation regressions; never query a workstation."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from progress_evidence import cli
from quick_status_summary import summarize, render


def fixture():
    return {'schema': 'vasp-quick-status/v1', 'query_status': 'PARTIAL', 'canonical_status': 'UNKNOWN',
            'state_consistency': 'UNKNOWN', 'observed_local_utc': '2026-11-10T00:00:00Z',
            'ssh': {'attempted': True, 'exit_code': 0, 'timed_out': False},
            'sources': {'status_json': {'raw_status': 'RUNNING'}},
            'progress': {'stdout': {'electronic_rows': [{'raw': 'DAV: 7'}]}},
            'processes': [{'pid': 7, 'state': 'PRESENT'}], 'tmux': {'state': 'PRESENT'},
            'reasons': ['RUNNING requires fresh stable calculation output and live task process context; status is not a heartbeat']}


class SummaryTests(unittest.TestCase):
    def test_unknown_is_not_upgraded_and_input_is_unchanged(self):
        snap = fixture()
        before = copy.deepcopy(snap)
        value = summarize(snap)
        self.assertEqual(value['judgment']['canonical_status'], 'UNKNOWN')
        self.assertIn('RUNNING_EVIDENCE_INSUFFICIENT', value['judgment']['reason_codes'])
        self.assertIn('DAV: 7', render(snap))
        self.assertEqual(snap, before)

    def test_reason_matrix(self):
        cases = [({'ssh': {'attempted': True, 'timed_out': True}}, 'QUERY_TIMEOUT'),
                 ({'ssh': {'attempted': True, 'exit_code': 255}}, 'TRANSPORT_INCOMPLETE'),
                 ({'state_consistency': 'CONFLICT'}, 'STATE_CONFLICT'),
                 ({'sources': {'status_json': {'identity_binding': {'state': 'MISMATCH'}}}}, 'SOURCE_IDENTITY_MISMATCH'),
                 ({'reasons': ['remote quick-status identity differs from the local manifest']}, 'REMOTE_IDENTITY_MISMATCH'),
                 ({'files': {'OUTCAR': {'stable': False, 'read_before': {'present': True}}}}, 'FILE_READ_UNSTABLE'),
                 ({'files': {'OUTCAR': {'truncated_prefix': True}}}, 'BOUNDED_TAIL'),
                 ({'sources': {'run_timing': {'lifecycle': 'TIMING_PENDING_UNTIL_EXIT'}}}, 'TIMING_PENDING_UNTIL_EXIT')]
        for change, code in cases:
            with self.subTest(code=code):
                snap = fixture(); snap.update(change)
                self.assertIn(code, summarize(snap)['judgment']['reason_codes'])

    def test_minimal_old_snapshot(self):
        snap = {'schema': 'vasp-quick-status/v1'}
        self.assertIsNone(summarize(snap)['progress']['last_ionic_row'])
        self.assertIn('UNKNOWN', render(snap))

    def test_terminal_state_is_preserved_with_nonzero_transport(self):
        snap = fixture(); snap['canonical_status'] = 'FAILED_OR_INCOMPLETE'
        snap['ssh']['exit_code'] = 255
        self.assertEqual(summarize(snap)['judgment']['canonical_status'], 'FAILED_OR_INCOMPLETE')

    def test_bad_schema_rejected(self):
        for snap in (None, [], {}, {'schema': 'other'}):
            with self.assertRaises(ValueError): summarize(snap)

    def test_offline_cli_never_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'snapshot.json'
            source.write_text(json.dumps(fixture()), encoding='utf-8')
            with patch('progress_evidence.quick_status') as query, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli(['summarize-status', '--snapshot', str(source)]), 0)
            query.assert_not_called()
            self.assertIn('规范判断：UNKNOWN', output.getvalue())

    def test_query_text_is_once_and_partial_exit_remains(self):
        with patch('progress_evidence.quick_status', return_value=fixture()) as query, redirect_stdout(io.StringIO()):
            self.assertEqual(cli(['quick-status', '--manifest', 'fixture.json', '--format', 'text']), 3)
        query.assert_called_once_with('fixture.json')


if __name__ == '__main__':
    unittest.main()
