"""Bounded local probing, frame extraction, and full-audio transcription."""
from pathlib import Path
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile

from storage import read_json, source_hash, write_json


def executable(config, name):
    configured = config.get(name)
    command = str(Path(configured).expanduser()) if configured else shutil.which(name)
    if not command:
        raise RuntimeError('Required media command is unavailable: ' + name)
    result = subprocess.run([command, '-version'], capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('Media command cannot run: ' + name)
    return command


def inspect_video(path, config):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('Video must be a regular file')
    ffprobe = executable(config, 'ffprobe')
    command = [ffprobe, '-v', 'error', '-show_entries',
               'format=duration,size:stream=codec_type,width,height,r_frame_rate:stream_tags=rotate:stream_side_data=rotation',
               '-of', 'json', str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Media probe failed')
    data = json.loads(result.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    duration = float(data.get('format', {}).get('duration', 0))
    if not video or not math.isfinite(duration) or duration <= 0:
        raise ValueError('A decodable video stream with a positive duration is required')
    width, height = int(video['width']), int(video['height'])
    rotation = float(video.get('tags', {}).get('rotate', 0))
    for side in video.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    if round(rotation) % 180 == 90:
        width, height = height, width
    rate = video.get('r_frame_rate', '0/1').split('/')
    fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 and float(rate[1]) else 0
    return {'source': str(path), 'sourceName': path.name, 'sourceSha256': source_hash(path),
            'bytes': path.stat().st_size, 'sourceMtimeNs': path.stat().st_mtime_ns,
            'durationSeconds': duration, 'width': width, 'height': height, 'fps': fps,
            'hasAudio': any(s.get('codec_type') == 'audio' for s in data.get('streams', [])),
            'route': 'service-client', 'analysisDate': config.get('analysis_date')}


def verify_source(metadata):
    path = Path(metadata['source'])
    if not path.is_file() or source_hash(path) != metadata['sourceSha256']:
        raise RuntimeError('Source video is missing or changed; create a fresh run for changed media')
    return path


def sample_frames(directory, config, count=None, at=None):
    directory = Path(directory)
    metadata = read_json(directory / 'metadata.json')
    source = verify_source(metadata)
    ffmpeg = executable(config, 'ffmpeg')
    duration = metadata['durationSeconds']
    if at is None:
        count = int(count or config.get('frame_count', 24))
        if not 1 <= count <= 40:
            raise ValueError('Frame count must be between 1 and 40')
        times = [duration * (i + 0.5) / count for i in range(count)]
    else:
        times = list(at)
    if any(not math.isfinite(t) or t < 0 or t >= duration for t in times):
        raise ValueError('Frame times must lie within the source duration')
    records = read_json(directory / 'frames.json', [])
    unique = {round(float(f['at']), 3): f for f in records}
    if len(set(unique) | {round(t, 3) for t in times}) > 40:
        raise ValueError('A single analysis supports at most 40 frames; split review into bounded stages')
    (directory / 'frames').mkdir(exist_ok=True)
    for requested in times:
        t = min(round(requested, 3), max(0, duration - 0.05))
        if t in unique and (directory / unique[t]['file']).is_file():
            continue
        target = directory / 'frames' / ('frame-' + str(round(t * 1000)).zfill(9) + '.jpg')
        result = subprocess.run([
            ffmpeg, '-v', 'error', '-nostdin', '-y', '-ss', str(t), '-i', str(source),
            '-frames:v', '1', '-vf',
            "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease",
            '-q:v', '3', str(target)], capture_output=True, timeout=40)
        if result.returncode or not target.is_file() or not target.stat().st_size:
            raise RuntimeError('Requested video frame could not be decoded')
        unique[t] = {'file': target.relative_to(directory).as_posix(), 'at': t}
    records = sorted(unique.values(), key=lambda f: f['at'])
    write_json(directory / 'frames.json', records)
    # Optional preview only; the inference and report do not depend on Pillow.
    try:
        from PIL import Image, ImageDraw
        sheet = Image.new('RGB', (1280, math.ceil(len(records) / 4) * 210), '#121a23')
        draw = ImageDraw.Draw(sheet)
        for i, frame in enumerate(records):
            with Image.open(directory / frame['file']) as image:
                image.thumbnail((314, 177))
                x, y = i % 4 * 320, i // 4 * 210
                sheet.paste(image, (x, y))
                draw.text((x + 7, y + 183), timestamp(frame['at']), fill='white')
        sheet.save(directory / 'contact-sheet.jpg')
    except ImportError:
        pass
    return records


def timestamp(seconds, srt=False):
    ms = round(float(seconds) * 1000)
    return (f'{ms // 3600000:02}:{ms % 3600000 // 60000:02}:'
            f'{ms % 60000 // 1000:02}' + (',' if srt else '.') + f'{ms % 1000:03}')


def write_transcript(directory, result):
    directory = Path(directory)
    write_json(directory / 'transcript.json', result)
    (directory / 'transcript.txt').write_text('\n'.join(
        '[' + timestamp(s['start']) + ' - ' + timestamp(s['end']) + '] ' + s.get('text', '').strip()
        for s in result['segments']), encoding='utf-8')
    (directory / 'transcript.srt').write_text('\n\n'.join(
        str(i + 1) + '\n' + timestamp(s['start'], True) + ' --> ' + timestamp(s['end'], True) + '\n' + s.get('text', '').strip()
        for i, s in enumerate(result['segments'])) + '\n', encoding='utf-8')


def transcribe(directory, client, config):
    directory = Path(directory)
    metadata = read_json(directory / 'metadata.json')
    source = verify_source(metadata)
    if (directory / 'transcript.json').exists():
        return read_json(directory / 'transcript.json')
    if not metadata.get('hasAudio', True):
        result = {'text': '', 'segments': [], 'words': [], 'timingQuality': [], 'audioStatus': 'no-audio'}
        write_transcript(directory, result)
        return result
    ffmpeg = executable(config, 'ffmpeg')
    duration = metadata['durationSeconds']
    chunk_seconds = int(config.get('audio_chunk_seconds', 600))
    if not 30 <= chunk_seconds <= 700:
        raise ValueError('Audio chunks must be between 30 and 700 seconds')
    # Prefer a nearby quiet point while keeping WAV inputs below the service limit.
    boundaries = [0.0]
    while duration - boundaries[-1] > chunk_seconds:
        target = boundaries[-1] + chunk_seconds
        low = max(boundaries[-1], target - 4)
        probe = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-ss', str(low),
            '-i', str(source), '-t', '8', '-vn', '-af', 'silencedetect=noise=-35dB:d=0.15',
            '-f', 'null', '-'], capture_output=True, text=True, timeout=40)
        candidates = [low + float(t) for t in re.findall(r'silence_end: ([\d.]+)', probe.stderr)] if probe.returncode == 0 else []
        candidates = [t for t in candidates if boundaries[-1] + 20 < t < duration]
        boundaries.append(min(candidates, key=lambda t: abs(t - target)) if candidates else target)
    boundaries.append(duration)
    plan = [{'start': start, 'end': end} for start, end in zip(boundaries, boundaries[1:])]
    write_json(directory / 'evidence' / 'audio-plan.json', plan)
    segments, words, texts, qualities = [], [], [], []
    with tempfile.TemporaryDirectory(prefix='zhicut-analysis-audio-') as temporary:
        for i, chunk in enumerate(plan):
            raw_path = directory / 'evidence' / ('asr-' + str(i).zfill(3) + '.json')
            journal = raw_path.with_name(raw_path.stem + '-state.json')
            if raw_path.exists():
                raw = read_json(raw_path)
            else:
                state = read_json(journal)
                if state:
                    raise RuntimeError('Previous transcription outcome requires review; no automatic replay')
                wav = Path(temporary) / ('audio-' + str(i) + '.wav')
                run = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-ss', str(chunk['start']),
                    '-i', str(source), '-t', str(chunk['end'] - chunk['start']), '-vn', '-ac', '1',
                    '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], capture_output=True, timeout=180)
                if run.returncode or not wav.is_file() or wav.stat().st_size > 25_000_000:
                    raise RuntimeError('Audio extraction failed or exceeded the Whisper input limit')
                state = {**chunk, 'sourceSha256': metadata['sourceSha256'], 'status': 'in_flight'}
                write_json(journal, state)
                try:
                    raw = client.transcribe(wav, config.get('source_language'))
                    if not isinstance(raw.get('text'), str) or not isinstance(raw.get('segments', []), list) or not isinstance(raw.get('words', []), list):
                        raise ValueError('Transcription response is missing its text or timestamp collections')
                    write_json(raw_path, raw)
                    write_json(journal, {**state, 'status': 'completed'})
                except BaseException:
                    write_json(journal, {**state, 'status': 'outcome_unknown'})
                    raise
            offset = chunk['start']
            qualities.append(raw.get('timing_quality', 'unspecified'))
            texts.append(raw.get('text', ''))
            for field, destination in [('segments', segments), ('words', words)]:
                for item in raw.get(field, []):
                    destination.append({**item, 'start': float(item['start']) + offset,
                                        'end': float(item['end']) + offset})
            if not raw.get('segments') and raw.get('text'):
                segments.append({'start': offset, 'end': chunk['end'], 'text': raw['text'], 'timingQuality': 'chunk-only'})
    result = {'text': '\n'.join(texts), 'segments': segments, 'words': words,
              'timingQuality': qualities, 'audioStatus': 'transcribed',
              'audioSubmittedSeconds': sum(c['end'] - c['start'] for c in plan)}
    write_transcript(directory, result)
    return result
