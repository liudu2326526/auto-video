import argparse
import json
import sys
from .core import validate, audit, save_new


def main():
    ap = argparse.ArgumentParser(description='Supervised shot/reference comparison. Never renders, drives UI, or scores taste.')
    sub = ap.add_subparsers(dest='command', required=True)
    v = sub.add_parser('validate'); v.add_argument('plan')
    a = sub.add_parser('audit'); a.add_argument('plan'); a.add_argument('--export', dest='exported', required=True)
    a.add_argument('--review', required=True); a.add_argument('--output', required=True)
    args = ap.parse_args()
    try:
        if args.command == 'validate':
            plan, base = validate(args.plan)
            result = {'status': 'valid', 'id': plan['id'], 'shot_count': len(plan['shots']),
                      'frames': base['output']['frames'], 'approval': False}
        else:
            result = audit(args.plan, args.exported, args.review); save_new(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result['status'] in {'needs_review', 'needs_revision'} else 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
