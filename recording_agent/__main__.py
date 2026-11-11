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
    bp = sub.add_parser('browser-probe', help='Experimental supervised fixture capture or metadata-only Gamma identity check; not third-party recording')
    bp.add_argument('--root', required=True)
    bp.add_argument('--kind', choices=['fixture', 'gamma-identity'], default='fixture')
    bp.add_argument('--test-case', choices=['normal', 'invalidate', 'overflow', 'late_grant'], default='normal')
    bp.add_argument('--timeout', type=int, default=120, help='Bounded local service lifetime, 30..180 seconds')
    bs = sub.add_parser('browser-probe-status', help='Read probe receipts without launching a browser or capture')
    bs.add_argument('--root', required=True)
    r = sub.add_parser('record', help='Synchronous bounded start; same request replays existing state')
    r.add_argument('--root', required=True); r.add_argument('--request-id', required=True)
    r.add_argument('--take-id', required=True); r.add_argument('--job-id', required=True)
    r.add_argument('--upstream-test-run-id', default=None, help='Reference only; helper does not verify a tool test')
    r.add_argument('--mode', choices=['fixture', 'picker', 'explicit'], required=True)
    r.add_argument('--validate-only', action='store_true', help='Validate source and exit without constructing a capture stream')
    r.add_argument('--duration', type=float, default=7)
    r.add_argument('--expected-bundle-id', default=None)
    r.add_argument('--expected-owner-pid', type=int, default=None)
    r.add_argument('--expected-window-id', type=int, default=None, help='CGWindowID from a prior source validation; not a browser window ID')
    r.add_argument('--expected-owner-launch-epoch', type=float, default=None)
    r.add_argument('--expected-display-id', type=int, default=None)
    r.add_argument('--expected-bounds', nargs=4, type=float, metavar=('X','Y','WIDTH','HEIGHT'))
    r.add_argument('--browser-ui-policy', choices=['local_raw_then_crop'], default=None,
                   help='Per-request opt-in only after user approval: local full Chrome window raw, followed by verified local crop')
    r.add_argument('--expected-window-title', default=None, help='Exact approved Chrome window title; required with local_raw_then_crop')
    for cmd in ('status', 'stop', 'recover'):
        c = sub.add_parser(cmd)
        c.add_argument('--root', required=True); c.add_argument('--take-id', required=True)
        if cmd == 'stop': c.add_argument('--request-id', required=True)
    args = p.parse_args()
    recorder = None
    try:
        if args.command in {'browser-probe', 'browser-probe-status'}:
            from .browser_probe import serve, read_status
            result = read_status(args.root) if args.command.endswith('-status') else serve(args.root, args.test_case, args.kind, args.timeout)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0 if args.command.endswith('-status') else (2 if result['state'] in {'unknown', 'blocked', 'discarded', 'quarantined'} else 0)
        if args.command == 'build':
            from .native import build
            print(json.dumps({'backend': str(build(args.root)), 'capture_started': False})); return 0
        recorder = Recorder(args.root)
        if args.command == 'record':
            spec = {'request_id': args.request_id, 'take_id': args.take_id, 'job_id': args.job_id,
                    'upstream_test_run_id': args.upstream_test_run_id, 'mode': args.mode,
                    'duration_seconds': args.duration, 'expected_bundle_id': args.expected_bundle_id,
                    'expected_owner_pid': args.expected_owner_pid}
            if args.validate_only: spec['validation_only'] = True
            for key in ('expected_window_id', 'expected_owner_launch_epoch', 'expected_display_id', 'expected_bounds', 'browser_ui_policy', 'expected_window_title'):
                value = getattr(args, key)
                if value is not None: spec[key] = value
            result = recorder.record(spec)
        elif args.command == 'status': result = recorder.status(args.take_id)
        elif args.command == 'stop': result = recorder.stop(args.take_id, args.request_id)
        else: result = recorder.recover(args.take_id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if args.command in {'status', 'stop'} or result['state'] in {'validated', 'source_validated'} else 2
    except (ValueError, OSError, TimeoutError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if recorder: recorder.close()


if __name__ == '__main__': sys.exit(main())
