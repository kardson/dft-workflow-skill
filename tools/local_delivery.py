"""Local presentation and static input archives over existing evidence checks."""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

from local_structure import digest, fresh, preview, write_json


def controlled_report(facts, review, workspace, output):
    root = Path(workspace).resolve()
    admin = Path(__file__).resolve().parent
    if not (admin / 'analysis.py').is_file():
        admin = admin.parent / 'agent_workflow'
    sys.path.insert(0, str(admin))
    import analysis
    # The original consumer validates preparation, backend, source bytes and
    # review before any HTML or geometry rendering can be published.
    delivery = analysis.render(facts, review, root, output)
    out = Path(output).resolve()
    facts_data = json.loads(Path(facts).read_text(encoding='utf-8'))
    review_data = json.loads(Path(review).read_text(encoding='utf-8'))
    recovered = json.loads(Path(facts_data['recovery_ref']).read_text(encoding='utf-8'))
    recovered_poscar = Path(recovered['analysis_source']) / 'POSCAR'
    images = []
    if 'POSCAR' in recovered.get('files', {}) and recovered_poscar.is_file():
        image = preview(recovered_poscar, out / 'structure-preview', 'Recovered geometry / source-bound preview')
        images.append(('Structure and constraints', Path(image['image'])))
    downloads = []
    for artifact in facts_data.get('artifacts', []):
        path = Path(artifact['path'])
        if path.suffix.lower() == '.png':
            images.append((path.name, path))
        elif path.suffix.lower() in {'.json', '.csv'} and path.stat().st_size <= 2_000_000:
            downloads.append((path.name, path.read_bytes()))
    analysis.sources(recovered, root)
    escape = html.escape
    cards = ''.join('<section><h2>' + escape(label) + '</h2><p>' + escape(str(review_data[key])) + '</p></section>'
                    for label, key in [('Result', 'answer'), ('Basis and confidence', 'confidence_and_basis'),
                                       ('Comparability', 'comparability'), ('Limits', 'limits'), ('Next action', 'next_action')])
    figures = ''.join('<figure><img alt="' + escape(label, quote=True) + '" src="data:image/png;base64,'
                      + base64.b64encode(path.read_bytes()).decode('ascii') + '"><figcaption>'
                      + escape(label) + '</figcaption></figure>' for label, path in images)
    links = ''.join('<li><a download="' + escape(name, quote=True) + '" href="data:application/octet-stream;base64,'
                    + base64.b64encode(data).decode('ascii') + '">' + escape(name) + '</a></li>' for name, data in downloads)
    text = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>VASP evidence report</title><style>body{font:16px system-ui,sans-serif;max-width:1100px;margin:36px auto;'
            'padding:0 24px;color:#203044;background:#f5f7fa}h1,h2{color:#142b49}section,figure,details{background:white;'
            'padding:20px;border-radius:12px;margin:18px 0}img{max-width:100%;height:auto}pre{white-space:pre-wrap;'
            'overflow-wrap:anywhere;font:13px ui-monospace,monospace}.limit{padding:14px;border-left:4px solid #ba7a13;'
            'background:#fff4dd}figcaption{font-size:13px;color:#52627a}</style><h1>'
            + escape(facts_data['research_question']) + '</h1><p class="limit">Source-bound software delivery. '
            'Scientific acceptance and new calculation authorization are not granted by this report.</p>' + cards + figures
            + '<section><h2>Embedded data</h2><ul>' + links + '</ul></section><details><summary>Original reviewed report</summary><pre>'
            + escape(Path(delivery['report']).read_text(encoding='utf-8')) + '</pre></details></html>')
    (out / 'report.html').write_text(text, encoding='utf-8')
    delivery['html_report'] = str(out / 'report.html')
    delivery['html_source_class'] = 'presentation_of_controlled_facts_and_review'
    delivery['html_data_embed_limit_bytes'] = 2_000_000
    write_json(out / 'delivery.json', delivery)
    return delivery


def archive_static(package, archive, receipt):
    from static_delivery_check import check_delivery_manifest, STATIC_PACKAGE_REQUIRED_FILES
    root = Path(package).resolve(strict=True)
    check_delivery_manifest(root)
    archive, receipt = Path(archive).resolve(), Path(receipt).resolve()
    if archive == receipt or archive.exists() or receipt.exists():
        raise ValueError('New distinct archive and receipt paths required')
    for target in (archive, receipt):
        if target == root or root in target.parents:
            raise ValueError('Keep archives and receipts outside original packages')
    identities = {}
    for line in (root / 'input_checksums.sha256').read_text().splitlines():
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+)', line)
        if not match:
            raise ValueError('Unsupported checksum line')
        name = match[2]
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name:
            raise ValueError('Unsafe archive path')
        identities[relative.as_posix()] = match[1].lower()
    for name in STATIC_PACKAGE_REQUIRED_FILES:
        identities.setdefault(name, digest(root / name))
    payload = {}
    for name, expected in identities.items():
        relative = PurePosixPath(name)
        if any(part.startswith('.') for part in relative.parts) or relative.name.upper() in {'POTCAR', 'WAVECAR', 'CHGCAR', 'CHG'}:
            raise ValueError('Sensitive/hidden members cannot enter this local input archive')
        path = root.joinpath(*relative.parts)
        path.resolve(strict=True).relative_to(root)
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent == root or root in parent.parents):
            raise ValueError('Linked archive members are unsupported')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Prepared file identity changed: ' + name)
        payload[name] = data
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as stream:
        for name, data in payload.items():
            stream.writestr(name, data)
    with zipfile.ZipFile(archive) as stream:
        if stream.testzip() is not None or any(stream.read(name) != data for name, data in payload.items()):
            raise ValueError('Archived bytes did not match prepared members')
    result = {'schema': 'vasp-local-input-archive/v1', 'package': str(root), 'archive': str(archive),
              'members': identities, 'archive_sha256': digest(archive), 'archive_crc_verified': True,
              'member_bytes_verified': True, 'jobs_submitted': 0, 'transfers_performed': 0,
              'scope': 'Validated static package only; no PAW or restart data'}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    write_json(receipt, result)
    return result
