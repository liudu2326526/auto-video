"""Local durable capture state and cooperative desktop lock, not an OS security gateway."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
from pathlib import Path
from .media import inspect_media, assess_duration

ACTIVE = {'starting', 'recording', 'stopping'}
TERMINAL = {'validated', 'partial', 'failed', 'quarantined', 'unknown'}
REPO = Path(__file__).resolve().parents[1]


class RecordingError(ValueError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise RecordingError('Identifiers must be 1..64 ASCII letters/digits/underscore/hyphen')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def atomic_json(path, data):
    path = Path(path)
    private_dir(path.parent)
    fd, temp = tempfile.mkstemp(prefix='.atomic-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.link(temp, path, follow_symlinks=False)  # Create-only, including broken symlink targets.
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def private_dir(path, create=False):
    path = Path(path)
    if create: path.mkdir(mode=0o700, exist_ok=True)
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise RecordingError('Directory must be current-user-owned and private (0700); no chmod attempted')
    return path


def private_file(path, single_link=True):
    s = Path(path).lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or (single_link and s.st_nlink != 1):
        raise RecordingError('File must be private, regular, owned and not a symlink/foreign hardlink')
    return path


def new_private_file(path):
    private_dir(Path(path).parent)
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w')


def root_path(value):
    raw = Path(value).absolute()
    # Refuse symlinks at any component, rather than silently changing output scope.
    for part in [raw, *raw.parents]:
        if part.is_symlink() and not (part in {Path('/tmp'), Path('/var')} and part.lstat().st_uid == 0):
            raise RecordingError('Runtime root cannot contain user-controlled symlinks')
    root = raw.resolve()
    if root == REPO or REPO in root.parents or any((p / '.git').exists() for p in [root, *root.parents]):
        raise RecordingError('Runtime state and media must be outside the repository')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_dir(root)
    return root


class DesktopLock:
    """Shared across runtime roots for this UID; inherited by native child until it exits."""
    def __init__(self, path=None):
        shared = Path('/private/tmp' if sys.platform == 'darwin' else '/tmp')
        self.path = Path(path or (shared / f'auto-video-desktop-{os.getuid()}.lock'))
        self.fd = None

    def __enter__(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
                raise RecordingError('Unsafe desktop lock file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd); raise RecordingError('DESKTOP_BUSY') from exc
        except BaseException:
            os.close(fd); raise
        self.fd = fd
        return self

    def __exit__(self, *args):
        # Do not LOCK_UN: an inherited child must keep the open-description lock.
        if self.fd is not None: os.close(self.fd); self.fd = None


def validate_spec(spec):
    expected = {'request_id', 'take_id', 'job_id', 'upstream_test_run_id', 'mode',
                'duration_seconds', 'expected_bundle_id', 'expected_owner_pid'}
    if set(spec) != expected: raise RecordingError('Unknown/missing recording parameters')
    for field in ('request_id', 'take_id', 'job_id'): identifier(spec[field])
    if spec['upstream_test_run_id'] is not None: identifier(spec['upstream_test_run_id'])
    duration = spec['duration_seconds']
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 1 <= duration <= 30:
        raise RecordingError('Duration must be finite and in 1..30 seconds')
    if spec['mode'] == 'fixture':
        if spec['expected_bundle_id'] is not None or spec['expected_owner_pid'] is not None:
            raise RecordingError('Fixture cannot select external owners')
    elif spec['mode'] == 'picker':
        bundle, pid = spec['expected_bundle_id'], spec['expected_owner_pid']
        if not isinstance(bundle, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,199}', bundle):
            raise RecordingError('Picker needs an explicit expected bundle ID')
        if type(pid) is not int or pid <= 0: raise RecordingError('Picker needs an explicit expected owner PID')
    else:
        raise RecordingError('Only fixture or system single-window picker modes are supported')
    return spec


class Store:
    def __init__(self, root):
        self.root = root_path(root)
        self.private = self.root / '.recording-state'
        private_dir(self.private, create=True)
        dbpath = self.private / 'state.sqlite'
        for p in [dbpath, Path(str(dbpath) + '-wal'), Path(str(dbpath) + '-shm')]:
            if p.exists() or p.is_symlink(): private_file(p)
        if not dbpath.exists():
            fd = os.open(dbpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(dbpath, timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS requests(
            request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, take_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS takes(take_id TEXT PRIMARY KEY, payload TEXT NOT NULL);''')
        for p in [dbpath, Path(str(dbpath) + '-wal'), Path(str(dbpath) + '-shm')]:
            if p.exists(): private_file(p)

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try: yield; self.db.commit()
        except BaseException: self.db.rollback(); raise

    def get(self, take_id):
        identifier(take_id)
        row = self.db.execute('SELECT payload FROM takes WHERE take_id=?', (take_id,)).fetchone()
        if not row: raise RecordingError('TAKE_NOT_FOUND')
        return json.loads(row[0])

    def put(self, payload):
        self.db.execute('UPDATE takes SET payload=? WHERE take_id=?', (canonical(payload), payload['take_id']))

    def replay(self, request_id, digest):
        row = self.db.execute('SELECT digest,take_id FROM requests WHERE request_id=?', (request_id,)).fetchone()
        if not row: return None
        if row[0] != digest: raise RecordingError('REQUEST_CONFLICT')
        return self.get(row[1])

    def folder(self, take_id):
        identifier(take_id)
        path = self.root / ('take-' + take_id)
        if path.is_symlink(): raise RecordingError('Symlink take folder refused')
        if path.resolve().parent != self.root: raise RecordingError('Output escaped runtime root')
        if path.exists(): private_dir(path)
        return path


class Recorder:
    def __init__(self, root, backend=None, validator=inspect_media, lock_path=None):
        self.store = Store(root)
        self.backend = backend
        self.validator = validator
        self.lock_path = lock_path  # Python test injection only, no CLI override.

    def close(self): self.store.db.close()

    def record(self, spec):
        validate_spec(spec)
        digest = hashlib.sha256(canonical({'operation': 'record', 'spec': spec}).encode()).hexdigest()
        old = self.store.replay(spec['request_id'], digest)
        if old: return self.status(old['take_id'])
        with DesktopLock(self.lock_path) as lease:
            with self.store.transaction():
                old = self.store.replay(spec['request_id'], digest)
                if old:
                    if old['state'] in ACTIVE:
                        old.update(state='unknown', reason='Replay acquired released desktop lease without terminal state')
                        self.store.put(old)
                    return old
                if self.store.db.execute('SELECT 1 FROM takes WHERE take_id=?', (spec['take_id'],)).fetchone():
                    raise RecordingError('TAKE_ALREADY_RESERVED')
                folder = self.store.folder(spec['take_id'])
                folder.mkdir(mode=0o700, exist_ok=False)
                private_dir(folder)
                take = {'schema_version': 1, 'take_id': spec['take_id'], 'job_id': spec['job_id'],
                        'spec': spec, 'state': 'starting', 'started_utc': now(),
                        'started_monotonic': time.monotonic(), 'heartbeat_utc': now(),
                        'tool_test_status': 'not_performed', 'supervised': True}
                self.store.db.execute('INSERT INTO requests VALUES(?,?,?)', (spec['request_id'], digest, spec['take_id']))
                self.store.db.execute('INSERT INTO takes VALUES(?,?)', (spec['take_id'], canonical(take)))
            try:
                if self.backend is None:
                    from .native import NativeBackend
                    self.backend = NativeBackend()
                result = self.backend.run(spec, folder, lease.fd, self._progress)
                return self._finish(spec['take_id'], result)
            except Exception as exc:
                with self.store.transaction():
                    take = self.store.get(spec['take_id'])
                    take.update(state='unknown', error=type(exc).__name__, reason=str(exc)[:500], ended_utc=now())
                    self.store.put(take)
                return take

    def _progress(self, take_id, events):
        with self.store.transaction():
            take = self.store.get(take_id)
            if take['state'] != 'stopping' and any(e.get('event') == 'recording_started' for e in events):
                take['state'] = 'recording'
            take['heartbeat_utc'] = now()
            self.store.put(take)

    def _finish(self, take_id, result):
        take = self.store.get(take_id)
        folder = self.store.folder(take_id)
        events = result['events']
        finished = [e for e in events if e.get('event') == 'recording_finished']
        sources = [e for e in events if e.get('event') == 'source_verified']
        failures = [e for e in events if e.get('event') in {'blocked_or_failed', 'interference', 'timeout_unknown'}]
        good = result.get('exit_code') == 0 and finished and sources and not failures and finished[-1].get('clean') is True
        take['ended_utc'] = now()
        take['backend_exit_code'] = result.get('exit_code')
        if not good:
            take['state'] = 'quarantined' if finished or failures else 'unknown'
            take['reason'] = failures[-1].get('reason', failures[-1]['event']) if failures else 'No confirmed clean backend completion'
        else:
            try:
                partial, target = folder / 'raw.partial.mp4', folder / 'raw.mp4'
                media_path = partial if partial.exists() else target
                private_file(media_path, single_link=False)
                stop_digest = hashlib.sha256(canonical({'operation': 'stop', 'take_id': take_id}).encode()).hexdigest()
                recorded_stop = self.store.db.execute('SELECT 1 FROM requests WHERE take_id=? AND digest=?', (take_id, stop_digest)).fetchone()
                explicit_stop = bool(recorded_stop) and any(e.get('event') == 'stop_intent' and e.get('reason') == 'requested' for e in events)
                media = self.validator(media_path, take['spec']['duration_seconds'], allow_early_stop=explicit_stop)
                media.update(assess_duration(media['duration_seconds'], take['spec']['duration_seconds'], explicit_stop))
                source = sources[-1]['source']
                if source.get('mode') != take['spec']['mode'] or not source.get('window_id') or not source.get('owner_pid'):
                    raise RecordingError('Invalid source identity')
                if take['spec']['mode'] == 'picker' and (source.get('bundle_id') != take['spec']['expected_bundle_id'] or source['owner_pid'] != take['spec']['expected_owner_pid']):
                    raise RecordingError('Recorded owner does not match approved target')
                if partial.exists():
                    if target.exists() or target.is_symlink():
                        private_file(target, single_link=False)
                        if not os.path.samefile(partial, target): raise RecordingError('Existing raw output is a different file')
                    else: os.link(partial, target)  # Exclusive publication, never overwrite.
                starts = [e for e in events if e.get('event') == 'recording_started']
                manifest = {'schema_version': 1, 'artifact_kind': 'recording_agent_take',
                            'take_id': take_id, 'job_id': take['job_id'],
                            'upstream_test_run_id': take['spec']['upstream_test_run_id'],
                            'tool_test_status': 'not_performed', 'supervised': True,
                            'plan': {'completion': media['plan_completion'], 'complete': media['plan_complete'],
                                     'target_seconds': take['spec']['duration_seconds'], 'duration_tolerance_seconds': 0.75,
                                     'explicit_stop_acknowledged': explicit_stop},
                            'source': source, 'media': media, 'raw_file': 'raw.mp4',
                            'timing': {'started_utc': take['started_utc'], 'ended_utc': take['ended_utc'],
                                       'supervisor_elapsed_ms': None if result.get('recovered') else round((time.monotonic() - take['started_monotonic']) * 1000),
                                       'recording_started_callback': starts[-1] if starts else None,
                                       'pts_mapping_quality': 'callback_anchor_only_not_frame_accurate'},
                            'integration': 'standalone; not a test_runs/capture_manifests SQL import',
                            'acceptance': 'media_validated; plan ' + media['plan_completion'] + '; visual review and real-tool test still required'}
                manifest_path = folder / 'manifest.json'
                if manifest_path.exists() or manifest_path.is_symlink():
                    private_file(manifest_path)
                    if manifest_path.stat().st_size > 2 * 1024 * 1024:
                        raise RecordingError('Unsafe existing manifest')
                    existing = json.loads(manifest_path.read_text())
                    if any(existing.get(k) != manifest.get(k) for k in ('schema_version', 'take_id', 'job_id', 'source', 'media', 'raw_file', 'tool_test_status', 'plan')):
                        raise RecordingError('Existing manifest conflicts with recovered recording')
                else:
                    with new_private_file(manifest_path) as f:
                        json.dump(manifest, f, ensure_ascii=False, indent=2, allow_nan=False); f.flush(); os.fsync(f.fileno())
                take.update(state='validated' if media['plan_complete'] else 'partial',
                            plan_completion=media['plan_completion'], plan_complete=media['plan_complete'],
                            manifest=str(manifest_path), media_sha256=media['sha256'])
            except Exception as exc:
                take.update(state='quarantined', reason='Media/publication/source validation failed: ' + str(exc)[:400])
        with self.store.transaction(): self.store.put(take)
        if take['state'] in {'validated', 'partial'}: (folder / 'raw.partial.mp4').unlink(missing_ok=True)
        return take

    def status(self, take_id):
        take = self.store.get(take_id)
        if take['state'] in ACTIVE:
            try:
                with DesktopLock(self.lock_path):
                    with self.store.transaction():
                        take = self.store.get(take_id)
                        if take['state'] in ACTIVE:
                            take.update(state='unknown', reason='Desktop lease released without durable terminal state')
                            self.store.put(take)
            except RecordingError as exc:
                if str(exc) != 'DESKTOP_BUSY': raise
                take['liveness_note'] = 'A desktop lease is held; PID/lease alone does not prove success'
                take['state_freshness'] = 'last_observed_only'
        return take

    def stop(self, take_id, request_id):
        identifier(request_id); identifier(take_id)
        digest = hashlib.sha256(canonical({'operation': 'stop', 'take_id': take_id}).encode()).hexdigest()
        with self.store.transaction():
            old = self.store.replay(request_id, digest)
            if old: return old
            take = self.store.get(take_id)
            self.store.db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, digest, take_id))
            if take['state'] in ACTIVE:
                marker = self.store.folder(take_id) / 'stop.json'
                expected_marker = {'take_id': take_id, 'operation': 'stop'}
                if marker.exists() or marker.is_symlink():
                    private_file(marker)
                    if marker.stat().st_size > 4096 or json.loads(marker.read_text()) != expected_marker:
                        raise RecordingError('Existing stop request conflicts; not overwritten')
                else: atomic_json(marker, expected_marker)
                take['state'] = 'stopping'; self.store.put(take)
        return self.status(take_id)

    def recover(self, take_id):
        """Reconcile only after both supervisor and inherited native lease are gone."""
        with DesktopLock(self.lock_path):
            take = self.store.get(take_id)
            if take['state'] in {'validated', 'partial', 'failed', 'quarantined'}: return take
            events_path = self.store.folder(take_id) / 'backend-events.jsonl'
            if events_path.exists() or events_path.is_symlink(): private_file(events_path)
            if not events_path.exists() or events_path.stat().st_size > 2 * 1024 * 1024:
                with self.store.transaction():
                    take.update(state='unknown', reason='No bounded backend completion log; manual reconciliation required')
                    self.store.put(take)
                return take
            events = []
            for line in events_path.read_text().splitlines():
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict): break
                    events.append(row)
                except json.JSONDecodeError: break
            exits = [e for e in events if e.get('event') == 'process_exit']
            return self._finish(take_id, {'events': events, 'exit_code': exits[-1].get('exit_code') if exits else None, 'recovered': True})
