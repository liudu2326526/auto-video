"""Inspect completed, local, silent recordings; never capture devices here."""
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
from pathlib import Path


class MediaError(ValueError):
    pass


def assess_duration(actual, target, allow_early_stop=False):
    # A calibration assumption, not a guarantee about every encoder or workload.
    tolerance = 0.75
    if not math.isfinite(actual) or not 0.05 <= actual <= target + tolerance:
        raise MediaError('Recording duration exceeds the valid target allowance')
    if not allow_early_stop and actual < target - tolerance:
        raise MediaError('Clean finish is unexpectedly shorter than the shooting plan')
    return {'plan_complete': not allow_early_stop,
            'plan_completion': 'stopped_early' if allow_early_stop else 'complete',
            'duration_tolerance_seconds': tolerance}


def inspect_media(path, duration_limit, runner=subprocess.run, *, allow_early_stop=False):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > 256 * 1024 * 1024:
        raise MediaError('Recording must be a nonempty regular file <=256 MiB; no symlinks')
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise MediaError('Recording must be owned by current user and private')
    probe = shutil.which('ffprobe')
    ffmpeg = shutil.which('ffmpeg')
    if not probe or not ffmpeg:
        raise MediaError('Existing ffprobe and ffmpeg are required; nothing will be installed')

    def command(args, timeout):
        r = runner(args, text=True, capture_output=True, timeout=timeout)
        if r.returncode:
            raise MediaError('Media inspection command failed')
        return r

    r = command([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], 15)
    try:
        metadata = json.loads(r.stdout)
        streams = metadata['streams']
        video = [s for s in streams if s.get('codec_type') == 'video']
        if len(video) != 1 or len(streams) != 1:
            raise MediaError('Exactly one video stream and zero audio/other streams required')
        v = video[0]
        duration = float(metadata['format']['duration'])
        width, height = int(v['width']), int(v['height'])
        if not math.isfinite(duration) or not 0.05 <= duration <= duration_limit + 3:
            raise MediaError('Duration is outside the bounded capture allowance')
        coverage = assess_duration(duration, duration_limit, allow_early_stop)
        if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 33554432):
            raise MediaError('Invalid or excessive recording dimensions')
        frames = command([probe, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                          '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(path)], 20)
        pts = [float(f['best_effort_timestamp_time']) for f in json.loads(frames.stdout)['frames']]
        if not pts or len(pts) > 10000 or not all(math.isfinite(t) for t in pts):
            raise MediaError('Missing/excessive/invalid presentation timestamps')
        if any(b <= a for a, b in zip(pts, pts[1:])):
            raise MediaError('Presentation timestamps must increase')
    except (KeyError, TypeError, json.JSONDecodeError, OverflowError) as exc:
        raise MediaError('Malformed ffprobe metadata') from exc
    decoded = command([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                       '-map', '0:v:0', '-f', 'null', '-'], 20 + duration_limit * 3)
    if decoded.stderr.strip():
        raise MediaError('Full decode emitted errors')
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return dict(coverage, **{'sha256': digest.hexdigest(), 'bytes': info.st_size, 'duration_seconds': duration,
            'width': width, 'height': height, 'codec': v.get('codec_name'), 'audio_streams': 0,
            'avg_frame_rate': v.get('avg_frame_rate'), 'time_base': v.get('time_base'),
            'presentation_timestamps_seconds': pts, 'full_decode_exit': 0,
            'visual_privacy_review': 'pending', 'cfr_30fps': 'not_certified'})
