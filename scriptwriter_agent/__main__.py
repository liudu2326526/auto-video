"""Local-only writer package export, with no model/API/browser side effects."""
import argparse
import json
import sys
from pathlib import Path
from .core import build_package, write_package


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates', required=True, help='Discovery candidates.json')
    parser.add_argument('--tool-id', required=True)
    parser.add_argument('--draft', required=True, help='Trusted writer draft JSON')
    parser.add_argument('--output', required=True, help='New output directory, never overwritten')
    parser.add_argument('--test-report')
    parser.add_argument('--evidence-root')
    args = parser.parse_args(argv)
    try:
        candidates = load(args.candidates)
        if not isinstance(candidates, list):
            raise ValueError('Candidates array required')
        matches = [c for c in candidates if isinstance(c, dict) and c.get('tool_id') == args.tool_id]
        if len(matches) != 1:
            raise ValueError('Expected exactly one matching candidate')
        result = build_package(matches[0], load(args.draft),
                               test_report=load(args.test_report) if args.test_report else None,
                               evidence_root=args.evidence_root)
        write_package(result, args.output)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f'Cannot export draft: {exc}', file=sys.stderr)
        return 2
    print(f'Draft exported for human review: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
