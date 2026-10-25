"""Portable local structure and controlled delivery tools, without host config."""
import argparse
import json
from pathlib import Path
from local_structure import candidates, prepare, preview
from local_delivery import archive_static, controlled_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for name in ('structure', 'candidates'):
        command = sub.add_parser(name)
        command.add_argument('--config', required=True)
        command.add_argument('--output-dir', required=True)
        if name == 'candidates': command.add_argument('--maximum-candidates', required=True, type=int)
    command = sub.add_parser('preview'); command.add_argument('--source', required=True)
    command.add_argument('--output-dir', required=True); command.add_argument('--title', required=True)
    command.add_argument('--format', choices=['vasp', 'cif'], default='vasp')
    command = sub.add_parser('report'); command.add_argument('--facts', required=True)
    command.add_argument('--review', required=True); command.add_argument('--workspace', required=True)
    command.add_argument('--output-dir', required=True)
    command = sub.add_parser('archive'); command.add_argument('--package', required=True)
    command.add_argument('--archive', required=True); command.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    if args.operation in ('structure', 'candidates'):
        path = Path(args.config).resolve(); config = json.loads(path.read_text(encoding='utf-8-sig'))
        result = (prepare(config, path.parent, args.output_dir) if args.operation == 'structure' else
                  candidates(config, path.parent, args.output_dir, args.maximum_candidates))
    elif args.operation == 'preview': result = preview(args.source, args.output_dir, args.title, fmt=args.format)
    elif args.operation == 'report': result = controlled_report(args.facts, args.review, args.workspace, args.output_dir)
    else: result = archive_static(args.package, args.archive, args.receipt)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
