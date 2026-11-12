"""Pure presentation of dated quick-status snapshots; no I/O or state inference."""
from __future__ import annotations

def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value if isinstance(value, list) else []


def summarize(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 'vasp-quick-status/v1':
        raise ValueError('Expected vasp-quick-status/v1 snapshot')
    ssh = _object(snapshot.get('ssh'))
    sources = _object(snapshot.get('sources'))
    files = _object(snapshot.get('files'))
    progress = _object(snapshot.get('progress'))
    reasons = [item for item in _rows(snapshot.get('reasons')) if isinstance(item, str)]
    codes = []

    def add(code):
        if code not in codes:
            codes.append(code)

    if ssh.get('reason') == 'manifest_identity_rejected':
        add('MANIFEST_IDENTITY_REJECTED')
    if ssh.get('timed_out') is True:
        add('QUERY_TIMEOUT')
    elif ssh.get('attempted') is True and ssh.get('exit_code') != 0:
        add('TRANSPORT_INCOMPLETE')
    if snapshot.get('state_consistency') == 'CONFLICT':
        add('STATE_CONFLICT')
    for source in sources.values():
        if _object(_object(source).get('identity_binding')).get('state') == 'MISMATCH':
            add('SOURCE_IDENTITY_MISMATCH')
    for reason in reasons:
        low = reason.lower()
        if 'identity differs' in low:
            add('REMOTE_IDENTITY_MISMATCH')
        if 'not valid json' in low or 'schema is missing or unsupported' in low:
            add('REMOTE_PAYLOAD_INVALID')
        if 'fresh stable calculation output' in low:
            add('RUNNING_EVIDENCE_INSUFFICIENT')
        if 'stale' in low:
            add('SOURCE_STALE')
    timing = _object(sources.get('run_timing'))
    if timing.get('lifecycle') == 'TIMING_PENDING_UNTIL_EXIT':
        add('TIMING_PENDING_UNTIL_EXIT')
    outputs = []
    for role, value in files.items():
        item = _object(value)
        if item.get('read_error'):
            add('FILE_READ_ERROR')
        if item.get('stable') is False and _object(item.get('read_before')).get('present') is True:
            add('FILE_READ_UNSTABLE')
        if _object(item.get('read_before')).get('present') is False:
            add('FILE_MISSING')
        if item.get('truncated_prefix') is True:
            add('BOUNDED_TAIL')
        if role in ('OUTCAR', 'OSZICAR', 'vasp.stdout', 'vasp.stderr'):
            outputs.append({'role': role, 'mtime_utc': _object(item.get('read_after') or item.get('read_before')).get('mtime_utc'),
                            'stable': item.get('stable'), 'coverage': item.get('coverage')})
    if snapshot.get('canonical_status') == 'UNKNOWN' and not codes:
        add('STATE_UNDETERMINED')
    stdout = _object(progress.get('stdout'))
    oszicar = _object(progress.get('oszicar'))

    def last(rows):
        rows = _rows(rows)
        return rows[-1] if rows else None

    return {'schema': 'vasp-quick-status-summary/v1',
            'observed_utc': snapshot.get('remote_observed_utc') or snapshot.get('observed_local_utc'),
            'transport': {'attempted': ssh.get('attempted'), 'exit_code': ssh.get('exit_code'), 'timed_out': ssh.get('timed_out')},
            'execution': {'declared_status': _object(sources.get('status_json')).get('raw_status'),
                          'processes': _rows(snapshot.get('processes')), 'tmux': _object(snapshot.get('tmux'))},
            'progress': {'last_electronic_row': last(stdout.get('electronic_rows')) or last(oszicar.get('tail_visible_electronic_rows')),
                         'last_ionic_row': oszicar.get('last_visible_complete_ionic_row') or last(stdout.get('ionic_rows')),
                         'outputs': outputs},
            'judgment': {'query_status': snapshot.get('query_status'), 'canonical_status': snapshot.get('canonical_status'),
                         'state_consistency': snapshot.get('state_consistency'), 'reason_codes': codes,
                         'reasons': reasons, 'scientific_acceptance': snapshot.get('scientific_acceptance', 'NOT_ASSESSED')},
            'limit': 'Dated observations only; presentation does not recompute status or establish convergence.'}


def render(snapshot):
    value = summarize(snapshot)
    transport, execution, progress, judgment = (value[key] for key in ('transport', 'execution', 'progress', 'judgment'))

    def shown(item):
        return 'UNKNOWN' if item is None else str(item)

    def row(item):
        return shown(item.get('raw')) if isinstance(item, dict) else shown(item)

    lines = ['观察时间：' + shown(value['observed_utc']),
             '连接与采集：attempted=' + shown(transport['attempted']) + '，SSH exit=' + shown(transport['exit_code'])
             + '，timeout=' + shown(transport['timed_out']) + '，query=' + shown(judgment['query_status']),
             '执行观察：声明=' + shown(execution['declared_status']) + '；tmux=' + shown(execution['tmux'].get('state'))
             + '；进程=' + (', '.join(shown(_object(p).get('pid')) + ':' + shown(_object(p).get('state')) for p in execution['processes']) or 'UNKNOWN'),
             '计算进展：最近可见电子记录=' + row(progress['last_electronic_row'])
             + '；最近可见离子记录=' + row(progress['last_ionic_row'])]
    for output in progress['outputs']:
        lines.append('输出 ' + output['role'] + '：mtime=' + shown(output['mtime_utc'])
                     + '，stable=' + shown(output['stable']) + '，coverage=' + shown(output['coverage']))
    lines.append('规范判断：' + shown(judgment['canonical_status']) + '；一致性=' + shown(judgment['state_consistency']))
    lines.append('原因分类：' + (', '.join(judgment['reason_codes']) or 'NONE_RECORDED'))
    lines.extend('限制原因：' + reason for reason in judgment['reasons'])
    lines.append('仅代表记录时刻和采集范围；进程存在不证明收敛。科学接受：' + shown(judgment['scientific_acceptance']))
    return '\n'.join(lines)
