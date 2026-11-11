import argparse
import json
import sys
from .core import EditError, event, prepare, review, validate, verify


def main():
    parser = argparse.ArgumentParser(description='Prepare/verify a supervised Mac Jianying edit; never drives the GUI.')
    sub = parser.add_subparsers(dest='command', required=True)
    v = sub.add_parser('validate')
    v.add_argument('plan')
    p = sub.add_parser('prepare')
    p.add_argument('plan')
    p.add_argument('--attempt', required=True)
    e = sub.add_parser('event')
    e.add_argument('attempt')
    e.add_argument('name')
    e.add_argument('--evidence', required=True)
    q = sub.add_parser('verify')
    q.add_argument('attempt')
    q.add_argument('--export', dest='exported', required=True)
    q.add_argument('--project', required=True)
    r = sub.add_parser('review')
    r.add_argument('attempt')
    r.add_argument('--export', dest='exported', required=True)
    r.add_argument('--evidence', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'validate':
            p, a = validate(args.plan)
            result = {'status': 'valid', 'plan_id': p['plan_id'], 'assets': list(a)}
        elif args.command == 'prepare':
            result = prepare(args.plan, args.attempt)
        elif args.command == 'event':
            result = event(args.attempt, args.name, args.evidence)
        elif args.command == 'review':
            result = review(args.attempt, args.exported, args.evidence)
        else:
            result = verify(args.attempt, args.exported, args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('status') in {'partial', 'rejected'} else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
