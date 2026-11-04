import argparse
import json
import sys
from .core import validate, audit, save_new


def main():
    ap = argparse.ArgumentParser(description='Supervised shot/reference comparison, optional original motion and local preset narration preparation. Never drives UI, renders the final edit, or scores taste.')
    sub = ap.add_subparsers(dest='command', required=True)
    v = sub.add_parser('validate'); v.add_argument('plan')
    a = sub.add_parser('audit'); a.add_argument('plan'); a.add_argument('--export', dest='exported', required=True)
    a.add_argument('--review', required=True); a.add_argument('--output', required=True)
    m = sub.add_parser('motion', help='Prepare a labelled original illustration in a declared annotation rectangle; never clears stock assets')
    m.add_argument('plan'); m.add_argument('--output', required=True)
    voice = sub.add_parser('voice', help='Prepare reviewed local Chinese preset narration in an explicitly provisioned environment; no listening approval')
    voice.add_argument('plan'); voice.add_argument('--output', required=True)
    retime = sub.add_parser('retime', help='Prepare explicit silent jump cuts/holds with a mandatory visible notice; never approves timing claims')
    retime.add_argument('plan'); retime.add_argument('--output', required=True)
    simulation = sub.add_parser('simulate', help='Render explicit viewport/occlusion assumptions; never approves actual phone readability')
    simulation.add_argument('plan'); simulation.add_argument('--output', required=True)
    args = ap.parse_args()
    try:
        if args.command == 'validate':
            plan, base = validate(args.plan)
            result = {'status': 'valid', 'id': plan['id'], 'shot_count': len(plan['shots']),
                      'frames': base['output']['frames'], 'approval': False}
        elif args.command == 'motion':
            from .motion import render_motion
            result = render_motion(args.plan, args.output)
        elif args.command == 'voice':
            from .voice import prepare_voice
            result = prepare_voice(args.plan, args.output)
        elif args.command == 'retime':
            from .retime import prepare_retime
            result = prepare_retime(args.plan, args.output)
        elif args.command == 'simulate':
            from .occlusion import simulate
            result = simulate(args.plan, args.output)
        else:
            result = audit(args.plan, args.exported, args.review); save_new(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result['status'] in {'needs_review', 'needs_revision'} else 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
