import argparse
import json
import os
import sys
from .core import Recorder, RecordingError


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description='Supervised silent single-window capture; no account access or real-tool success claims.')
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='Compile bundled backend only; does not start it')
    b.add_argument('--root', required=True, help='Private runtime directory outside this repository')
    r = sub.add_parser('record', help='Synchronous bounded start; same request replays existing state')
    r.add_argument('--root', required=True); r.add_argument('--request-id', required=True)
    r.add_argument('--take-id', required=True); r.add_argument('--job-id', required=True)
    r.add_argument('--upstream-test-run-id', default=None, help='Reference only; helper does not verify a tool test')
    r.add_argument('--mode', choices=['fixture', 'picker'], required=True)
    r.add_argument('--duration', type=float, default=7)
    r.add_argument('--expected-bundle-id', default=None)
    r.add_argument('--expected-owner-pid', type=int, default=None)
    for cmd in ('status', 'stop', 'recover'):
        c = sub.add_parser(cmd)
        c.add_argument('--root', required=True); c.add_argument('--take-id', required=True)
        if cmd == 'stop': c.add_argument('--request-id', required=True)
    args = p.parse_args()
    recorder = None
    try:
        if args.command == 'build':
            from .native import build
            print(json.dumps({'backend': str(build(args.root)), 'capture_started': False})); return 0
        recorder = Recorder(args.root)
        if args.command == 'record':
            spec = {'request_id': args.request_id, 'take_id': args.take_id, 'job_id': args.job_id,
                    'upstream_test_run_id': args.upstream_test_run_id, 'mode': args.mode,
                    'duration_seconds': args.duration, 'expected_bundle_id': args.expected_bundle_id,
                    'expected_owner_pid': args.expected_owner_pid}
            result = recorder.record(spec)
        elif args.command == 'status': result = recorder.status(args.take_id)
        elif args.command == 'stop': result = recorder.stop(args.take_id, args.request_id)
        else: result = recorder.recover(args.take_id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if args.command in {'status', 'stop'} or result['state'] == 'validated' else 2
    except (ValueError, OSError, TimeoutError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if recorder: recorder.close()


if __name__ == '__main__': sys.exit(main())
