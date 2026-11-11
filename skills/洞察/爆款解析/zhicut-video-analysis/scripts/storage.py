"""Versioned artifact storage and catalog; no credentials belong in this module."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib
import json
import os
import re
import shutil
import time
import uuid

SKILL_ROOT = Path(__file__).resolve().parent.parent


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_json(path, value, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def source_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def video_key(name, digest):
    stem = Path(name).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', stem).strip(' .-')[:60]
    return (stem or 'video') + '--' + digest[:12]


def new_run(output_root, metadata):
    root = Path(output_root).expanduser().resolve()
    key = video_key(metadata.get('sourceName', metadata['source']), metadata['sourceSha256'])
    now = datetime.now(timezone.utc)
    run_id = now.strftime('%Y%m%dT%H%M%SZ') + '--' + uuid.uuid4().hex[:8]
    directory = root / key / run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'evidence').mkdir()
    write_json(directory / 'metadata.json', metadata)
    write_json(directory / 'run.json', {
        'schemaVersion': 1, 'videoKey': key, 'videoName': Path(metadata['source']).name,
        'runId': run_id, 'createdAt': now.isoformat(), 'status': 'prepared',
        'durationSeconds': metadata.get('durationSeconds'), 'outputRoot': str(root),
    })
    return directory


def set_status(directory, status, **extra):
    directory = Path(directory)
    manifest = read_json(directory / 'run.json', {})
    manifest.update(status=status, updatedAt=datetime.now(timezone.utc).isoformat(), **extra)
    write_json(directory / 'run.json', manifest)
    return manifest


@contextmanager
def directory_lock(directory, name='.run.lock', timeout=10):
    path = Path(directory) / name
    started = time.monotonic()
    while True:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(str(os.getpid()))
            break
        except FileExistsError:
            if time.monotonic() - started >= timeout:
                raise RuntimeError('Artifact directory is locked; inspect its owner before removing the lock')
            time.sleep(0.05)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def rebuild_catalog(output_root, locale='zh-CN'):
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with directory_lock(root, '.catalog.lock'):
        entries = []
        for path in root.glob('*/*/run.json'):
            item = read_json(path, {})
            report = path.parent / 'report.html'
            entries.append({key: item.get(key) for key in (
                'videoKey', 'videoName', 'runId', 'status', 'createdAt', 'durationSeconds')})
            entries[-1]['report'] = str(report.relative_to(root)) if report.exists() else None
        entries.sort(key=lambda item: item.get('createdAt') or '', reverse=True)
        write_json(root / 'index.json', {'schemaVersion': 1, 'entries': entries})
        importlib.import_module('report').render_index(root, entries, locale)
    return root / 'index.html'


def import_existing(old_directory, output_root, locale='zh-CN'):
    """Copy a completed report without changing its source or issuing remote requests."""
    old = Path(old_directory).expanduser().resolve()
    metadata = read_json(old / 'metadata.json')
    if not metadata or not (old / 'analysis.json').is_file():
        raise ValueError('Existing metadata.json and analysis.json are required')
    verification = read_json(old / 'verification.json', {})
    digest = metadata.get('sourceSha256') or verification.get('sourceSha256')
    if not digest:
        digest = source_hash(metadata['source'])
    metadata['sourceSha256'] = digest
    metadata['importedFrom'] = str(old)
    directory = new_run(output_root, metadata)
    copied = [
        'analysis.json', 'analysis.txt', 'transcript.json', 'transcript.txt', 'transcript.srt',
        'contact-sheet.jpg', 'video_environment.json', 'verification.json',
    ]
    for name in copied:
        if (old / name).is_file():
            shutil.copy2(old / name, directory / name)
    frames = read_json(old / 'frames.json', [])
    normalized = []
    (directory / 'frames').mkdir()
    for index, frame in enumerate(frames):
        path = Path(frame['file'])
        original_path = path if path.is_absolute() else old / path
        path = original_path.resolve()
        if not path.is_relative_to(old) or not path.is_file():
            raise ValueError('Imported frame must be a regular file within its original report directory')
        # Preserve lexical local names because legacy HTML may refer to them.
        relative = Path(os.path.relpath(original_path, old))
        target = directory / relative
        if not target.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Imported frame destination escapes its run directory')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        normalized.append({**frame, 'file': target.relative_to(directory).as_posix()})
    write_json(directory / 'frames.json', normalized)
    # Keep source HTML verbatim, so prior manual insight additions remain intact.
    html_files = sorted(old.glob('*.html'))
    source_report = old / 'report.html' if (old / 'report.html').exists() else next(iter(html_files), None)
    if not source_report:
        raise ValueError('Existing HTML report is required')
    shutil.copy2(source_report, directory / 'report.html')
    for path in old.iterdir():
        if path.is_file() and path.suffix in ('.json', '.jsonl', '.txt') and path.name not in copied + ['metadata.json', 'frames.json']:
            shutil.copy2(path, directory / 'evidence' / path.name)
    write_json(directory / 'import-verification.json', {
        'originalUnchanged': True, 'copiedFrames': len(normalized),
        'reportSha256': source_hash(directory / 'report.html'),
        'originalReportSha256': source_hash(source_report),
    })
    set_status(directory, 'completed', imported=True)
    rebuild_catalog(output_root, locale)
    return directory
