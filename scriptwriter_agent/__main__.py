"""Local-only writer package export, with no model/API/browser side effects."""
import argparse
import json
import math
import sys
from pathlib import Path
from .core import build_package, write_package


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('Nonfinite JSON constant forbidden: ' + value)
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Nonfinite JSON number forbidden')
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique,
                      parse_constant=invalid_constant, parse_float=finite_float)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates', required=True, help='Discovery candidates.json')
    parser.add_argument('--tool-id', required=True)
    parser.add_argument('--draft', required=True, help='Trusted writer draft JSON')
    parser.add_argument('--output', required=True, help='New output directory, never overwritten')
    parser.add_argument('--test-report')
    parser.add_argument('--evidence-root')
    parser.add_argument('--media-catalog', help='Schema1 local observed video catalog; draft schema2 only')
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
                               evidence_root=args.evidence_root,
                               media_catalog=load(args.media_catalog) if args.media_catalog else None)
        write_package(result, args.output)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f'Cannot export draft: {exc}', file=sys.stderr)
        return 2
    print(f'Draft exported for human review: {args.output}; materials={result["story_readiness"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
