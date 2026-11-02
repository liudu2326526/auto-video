"""Compile/use only the bundled Swift backend. No device enumeration or network traffic."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from .core import RecordingError, atomic_json, root_path, private_dir, private_file, new_private_file

SOURCE = Path(__file__).parent / 'native' / 'WindowRecorder.swift'
IDENTITY_SOURCE = Path(__file__).parent / 'native' / 'IdentityPolicy.swift'


def build(root):
    if sys.platform != 'darwin': raise RecordingError('Native capture requires macOS 15.2+')
    compiler = shutil.which('swiftc')
    if not compiler: raise RecordingError('Existing Swift/Xcode SDK required; no installation attempted')
    root = root_path(root)
    private = root / '.recording-state'
    private_dir(private, create=True)
    private_dir(private / 'build', create=True)
    combined_source = IDENTITY_SOURCE.read_text() + '\n' + SOURCE.read_text()
    digest = hashlib.sha256(combined_source.encode()).hexdigest()
    folder = private / 'build' / digest
    private_dir(folder, create=True)
    target = folder / 'WindowRecorder'
    if target.exists():
        private_file(target)
        return target
    # Separate output name avoids publishing incomplete compiler output.
    temp = folder / ('WindowRecorder-' + str(os.getpid()))
    combined = folder / ('WindowRecorder-' + str(os.getpid()) + '.swift')
    with new_private_file(combined) as f: f.write(combined_source)
    r = subprocess.run([compiler, '-swift-version', '5', '-target', 'arm64-apple-macos15.2',
                        '-module-cache-path', str(folder / 'modules'), str(combined), '-o', str(temp)],
                       text=True, capture_output=True, timeout=120, umask=0o077)
    if r.returncode: raise RecordingError('Swift backend compilation failed: ' + r.stderr[-4000:])
    private_file(temp)
    try: os.link(temp, target)
    except FileExistsError: pass
    finally: temp.unlink(missing_ok=True)
    return target


def read_events(path):
    private_file(path)
    if path.stat().st_size > 2 * 1024 * 1024: raise RecordingError('Backend event log exceeded limit')
    rows = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict): raise RecordingError('Invalid backend event')
            rows.append(row)
        except json.JSONDecodeError: break  # A partially written last line is not a terminal event.
    return rows


class NativeBackend:
    def run(self, spec, folder, lease_fd, progress):
        binary = build(folder.parent)
        config = dict(spec, output=str(folder / 'raw.partial.mp4'), stop_file=str(folder / 'stop.json'),
                      lease_fd=lease_fd, parent_pid=os.getpid())
        config_path = folder / 'backend-config.json'
        atomic_json(config_path, config)
        event_path = folder / 'backend-events.jsonl'
        with new_private_file(event_path) as out, new_private_file(folder / 'backend-stderr.log') as err:
            proc = subprocess.Popen([str(binary), str(config_path)], stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=err, pass_fds=(lease_fd,), umask=0o077)
            deadline = time.monotonic() + spec['duration_seconds'] + 75
            try:
                while proc.poll() is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Backend did not terminate within bound')
                    progress(spec['take_id'], read_events(event_path))
                    time.sleep(0.1)
                return {'exit_code': proc.returncode, 'events': read_events(event_path)}
            except BaseException:
                # Only our own Popen child; never signal arbitrary PIDs from a state file.
                proc.terminate()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
                raise
