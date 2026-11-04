"""No GUI input, uploads, draft injection, or final video rendering in this helper."""
import hashlib
import json
import math
import re
import shutil
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path


class EditError(ValueError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EditError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    try:
        return json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(EditError('Nonfinite JSON number')))
    except (OSError, json.JSONDecodeError) as exc:
        raise EditError(str(exc)) from exc


def fields(obj, required, optional=()):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or set(required) - set(obj):
        raise EditError('Invalid/unsupported fields; expected ' + ', '.join(required))


def integer(value, low=0, high=18000):
    if type(value) is not int or not low <= value <= high:
        raise EditError('Expected bounded integer (bool/fraction not accepted)')
    return value


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise EditError('Invalid identity')
    return value


def local_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise EditError('Expected nonempty local regular file: ' + str(path))
    if path.stat().st_size > 2 * 1024 ** 3:
        raise EditError('Asset exceeds 2 GiB MVP limit')
    return path.resolve()


def run(args, timeout=120):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EditError('Command unavailable or timed out: ' + str(args[0])) from exc
    if result.returncode:
        raise EditError('Command failed: ' + str(args[0]) + ': ' + result.stderr[-1500:])
    return result.stdout


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-count_frames', '-show_streams',
                           '-show_format', '-of', 'json', str(path)]))


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def validate(plan_path):
    """Separate versioned GUI contract; never silently consume historic FFmpeg plans."""
    plan_path = Path(plan_path).resolve()
    p = read_json(plan_path)
    fields(p, ['schema', 'plan_id', 'script', 'output', 'assets', 'video', 'subtitles', 'narration', 'transitions'])
    if p['schema'] != 'jianying_edit_plan/v1':
        raise EditError('Unsupported plan schema')
    identity(p['plan_id'])
    o = p['output']
    fields(o, ['width', 'height', 'fps', 'frames'])
    integer(o['width'], 240, 3840)
    integer(o['height'], 240, 3840)
    if o['width'] % 2 or o['height'] % 2 or type(o['fps']) is not int or o['fps'] != 30:
        raise EditError('MVP requires even dimensions and integer 30 fps')
    total = integer(o['frames'], 1, 5400)
    fields(p['script'], ['version', 'lines'])
    identity(p['script']['version'])
    lines = p['script']['lines']
    if not isinstance(lines, dict) or not lines:
        raise EditError('Frozen script lines required')
    for line_id, text in lines.items():
        identity(line_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 300 or any(ord(c) < 32 for c in text):
            raise EditError('Invalid script text')
    if not isinstance(p['assets'], dict) or not p['assets']:
        raise EditError('Local assets required')
    assets = {}
    for aid, a in p['assets'].items():
        identity(aid)
        fields(a, ['path', 'sha256', 'rights', 'rights_basis'])
        if (a['rights'] not in {'cleared', 'first_party_owned', 'public_domain'}
                or not isinstance(a['rights_basis'], str) or not a['rights_basis'].strip()):
            raise EditError('Usage rights must be recorded')
        if not isinstance(a['path'], str) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', a['path']):
            raise EditError('Only local file paths accepted')
        path = local_file(plan_path.parent / a['path'])
        if digest(path) != a['sha256']:
            raise EditError('Asset hash changed: ' + aid)
        assets[aid] = {'path': str(path), 'sha256': a['sha256'], 'media': probe(path)}
    if not isinstance(p['video'], list) or not 1 <= len(p['video']) <= 30:
        raise EditError('Expected 1..30 video clips')
    cursor, clip_ids, checked = 0, set(), set()
    for c in p['video']:
        fields(c, ['id', 'asset_id', 'source_in_frame', 'source_out_frame', 'timeline_in_frame', 'timeline_out_frame'])
        identity(c['id'])
        if c['id'] in clip_ids:
            raise EditError('Duplicate clip identity')
        clip_ids.add(c['id'])
        if c['asset_id'] not in assets:
            raise EditError('Unknown video asset')
        a = assets[c['asset_id']]
        streams = [s for s in a['media']['streams'] if s['codec_type'] == 'video']
        if len(streams) != 1:
            raise EditError('Exactly one video stream per source required')
        v = streams[0]
        if v.get('avg_frame_rate') != '30/1' or v.get('r_frame_rate') != '30/1':
            raise EditError('Source must be 30 fps; no implicit speed changes')
        if c['asset_id'] not in checked:
            raw = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                  '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', a['path']]))
            pts = [float(f['best_effort_timestamp_time']) for f in raw['frames']]
            # CFR containers may quantize 1/30 second into alternating 33/34ms ticks.
            tick = float(Fraction(v.get('time_base', '1/1000000')))
            if (not 0 < tick <= 1 / 30 or not pts or len(pts) > 18000
                    or any(not math.isfinite(t) for t in pts)
                    or any(b <= a for a, b in zip(pts, pts[1:]))
                    or any(abs(t - pts[0] - i / 30) > tick + 0.000001 for i, t in enumerate(pts))):
                raise EditError('Source is not bounded CFR 30 fps')
            checked.add(c['asset_id'])
        start = integer(c['source_in_frame'])
        end = integer(c['source_out_frame'], 1)
        ti = integer(c['timeline_in_frame'])
        to = integer(c['timeline_out_frame'], 1)
        if not start < end <= int(v['nb_read_frames']) or ti != cursor or to - ti != end - start or to > total:
            raise EditError('Source out of bounds, gap, overlap, or implicit speed change')
        cursor = to
    if cursor != total:
        raise EditError('Video must cover output exactly')
    cursor, used_lines = 0, set()
    if not isinstance(p['subtitles'], list) or not p['subtitles']:
        raise EditError('Subtitles required')
    for cue in p['subtitles']:
        fields(cue, ['line_id', 'text', 'in_frame', 'out_frame'])
        start = integer(cue['in_frame'])
        end = integer(cue['out_frame'], 1)
        if not cursor <= start < end <= total or lines.get(cue['line_id']) != cue['text'] or cue['line_id'] in used_lines:
            raise EditError('Subtitle timing or frozen script mismatch')
        cursor = end
        used_lines.add(cue['line_id'])
    n = p['narration']
    if not isinstance(n, dict):
        raise EditError('Narration required')
    if n.get('mode') == 'macos_say':
        fields(n, ['mode', 'voice', 'rate'])
        if n['voice'] not in {'Tingting', 'Samantha'}:
            raise EditError('MVP supports installed Tingting/Samantha voices')
        integer(n['rate'], 100, 250)
        if not shutil.which('say'):
            raise EditError('Local macOS say unavailable')
    elif n.get('mode') == 'provided':
        fields(n, ['mode', 'asset_id'])
        if n['asset_id'] not in assets:
            raise EditError('Unknown narration asset')
        m = assets[n['asset_id']]['media']
        if not any(s['codec_type'] == 'audio' for s in m['streams']) or abs(float(m['format']['duration']) - total / 30) > 1 / 30:
            raise EditError('Provided narration must match full timeline length')
    else:
        raise EditError('Unsupported narration mode')
    seams = {c['timeline_out_frame'] for c in p['video'][:-1]}
    if not isinstance(p['transitions'], list):
        raise EditError('Expected transition list')
    seen = set()
    for t in p['transitions']:
        fields(t, ['at_frame', 'kind', 'duration_frames', 'timing'])
        integer(t['at_frame'], 1)
        integer(t['duration_frames'], 1, 30)
        if t['at_frame'] not in seams or t['at_frame'] in seen or t['kind'] != 'dissolve' or t['timing'] != 'preserve_total':
            raise EditError('Unsupported or duplicate native transition')
        if t['duration_frames'] > min(c['timeline_out_frame'] - c['timeline_in_frame'] for c in p['video']):
            raise EditError('Transition exceeds clip length')
        seen.add(t['at_frame'])
    return p, assets


def stamp(frames):
    ms = round(frames * 1000 / 30)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def prepare(plan_path, attempt):
    p, assets = validate(plan_path)
    attempt = Path(attempt).resolve()
    attempt.mkdir(parents=True, exist_ok=False)
    try:
        write_json(attempt / 'state.json', {'status': 'preparing', 'plan_id': p['plan_id']})
        write_json(attempt / 'edit_plan.json', p)
        write_json(attempt / 'sources.json', assets)
        o = p['output']
        imports = []
        for index, c in enumerate(p['video'], 1):
            dest = attempt / f'{index:02}-{c["id"]}.mp4'
            vf = (f'trim=start_frame={c["source_in_frame"]}:end_frame={c["source_out_frame"]},'
                  f'setpts=N/(30*TB),scale={o["width"]}:{o["height"]}:force_original_aspect_ratio=decrease,'
                  f'pad={o["width"]}:{o["height"]}:(ow-iw)/2:(oh-ih)/2,setsar=1')
            run(['ffmpeg', '-nostdin', '-v', 'error', '-i', assets[c['asset_id']]['path'], '-vf', vf,
                 '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-n', str(dest)])
            imports.append({'path': str(dest), 'sha256': digest(dest), 'clip': c})
        srt = '\n\n'.join(f'{i}\n{stamp(c["in_frame"])} --> {stamp(c["out_frame"])}\n{c["text"]}'
                            for i, c in enumerate(p['subtitles'], 1)) + '\n'
        (attempt / 'subtitles.srt').write_text(srt, encoding='utf-8-sig')
        voice = attempt / 'narration.wav'
        durations = []
        if p['narration']['mode'] == 'provided':
            run(['ffmpeg', '-nostdin', '-v', 'error', '-i', assets[p['narration']['asset_id']]['path'],
                 '-vn', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', '-n', str(voice)])
        else:
            args, filters, labels = ['ffmpeg', '-nostdin', '-v', 'error'], [], []
            for i, cue in enumerate(p['subtitles']):
                raw = attempt / f'voice-{i + 1:02}.aiff'
                text_file = attempt / f'voice-{i + 1:02}.txt'
                text_file.write_text(cue['text'])
                run(['say', '-v', p['narration']['voice'], '-r', str(p['narration']['rate']),
                     '-o', str(raw), '-f', str(text_file)])
                duration = float(probe(raw)['format']['duration'])
                if not math.isfinite(duration) or duration > (cue['out_frame'] - cue['in_frame']) / 30:
                    raise EditError('Narration exceeds cue; revise timing or script, never silently cut speech')
                durations.append({'line_id': cue['line_id'], 'speech_seconds': duration,
                                  'timeline_in_frame': cue['in_frame']})
                args.extend(['-i', str(raw)])
                delay = cue['in_frame'] * 1600
                filters.append(f'[{i}:a]aresample=48000,adelay={delay}S:all=1[a{i}]')
                labels.append(f'[a{i}]')
            filters.append(''.join(labels) + f'amix=inputs={len(labels)}:normalize=0,apad,atrim=end_sample={o["frames"] * 1600}[out]')
            run(args + ['-filter_complex', ';'.join(filters), '-map', '[out]', '-ar', '48000', '-ac', '2',
                        '-c:a', 'pcm_s16le', '-n', str(voice)])
        manifest = {'schema': 'jianying_import_package/v1', 'plan_id': p['plan_id'], 'output': o,
                    'clips': imports, 'subtitles': str(attempt / 'subtitles.srt'), 'narration': str(voice),
                    'narration_sha256': digest(voice), 'speech_alignment': durations,
                    'native_transitions': p['transitions'], 'editor': 'com.lemon.lvpro',
                    'final_video_rendered': False}
        write_json(attempt / 'import_package.json', manifest)
        write_json(attempt / 'state.json', {'status': 'prepared', 'plan_id': p['plan_id']})
        return manifest
    except Exception as exc:
        write_json(attempt / 'state.json', {'status': 'failed_preparation', 'error': str(exc)})
        raise


GUI_EVENTS = ['project_created', 'clips_on_timeline', 'subtitles_on_timeline', 'narration_on_timeline',
              'transitions_applied', 'export_requested', 'export_completed', 'project_reopened',
              'visual_review_passed', 'audio_review_passed']


def event(attempt, name, evidence_path):
    attempt = Path(attempt).resolve()
    if name not in GUI_EVENTS + ['needs_input', 'export_unknown', 'blocker_resolved']:
        raise EditError('Unknown event')
    if read_json(attempt / 'state.json')['status'] == 'failed_preparation':
        raise EditError('Cannot edit a failed import package')
    evidence = local_file(evidence_path)
    receipt = read_json(evidence)
    fields(receipt, ['event', 'app', 'project_name', 'observed', 'details'], ['export_path', 'project_path'])
    if (receipt['event'] != name or receipt['app'] != 'com.lemon.lvpro'
            or not isinstance(receipt['project_name'], str) or not receipt['project_name'].strip()
            or not isinstance(receipt['observed'], str) or not receipt['observed'].strip()
            or not isinstance(receipt['details'], str) or not receipt['details'].strip()):
        raise EditError('Evidence must contain the actual observed Jianying state')
    if name in {'export_requested', 'export_completed'} and not receipt.get('export_path'):
        raise EditError('Export events require the exact output path')
    if name == 'project_reopened' and not receipt.get('project_path'):
        raise EditError('Reopen event requires exact native draft directory')
    # Each caller supplies an observed AX fact, screenshot, or review receipt; not an auto GUI assertion.
    entry = {'event': name, 'at': datetime.now(timezone.utc).isoformat(),
             'evidence': str(evidence), 'sha256': digest(evidence), 'receipt': receipt}
    with (attempt / 'events.jsonl').open('a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    write_json(attempt / 'state.json', {'status': name, 'plan_id': read_json(attempt / 'edit_plan.json')['plan_id']})
    return entry


def verify(attempt, exported, project):
    attempt = Path(attempt).resolve()
    p = read_json(attempt / 'edit_plan.json')
    exported = local_file(exported)
    project = Path(project).resolve()
    if not project.is_dir() or not any(project.iterdir()):
        raise EditError('Exact nonempty native project directory required')
    project_files = [f for f in project.rglob('*') if f.is_file() and not f.is_symlink()]
    if not any(f.name in {'draft_content.json', 'draft_info.json'} for f in project_files):
        raise EditError('Native Jianying draft identity not found')
    m = probe(exported)
    v = [s for s in m['streams'] if s['codec_type'] == 'video']
    a = [s for s in m['streams'] if s['codec_type'] == 'audio']
    o = p['output']
    if len(v) != 1 or len(a) != 1 or (v[0]['width'], v[0]['height']) != (o['width'], o['height']):
        raise EditError('Export dimensions or audio/video stream counts mismatch')
    if v[0]['avg_frame_rate'] != '30/1' or int(v[0]['nb_read_frames']) != o['frames']:
        raise EditError('Export fps/frame count mismatch; reconcile native transition timing')
    if abs(float(m['format']['duration']) - o['frames'] / 30) > 0.08:
        raise EditError('Export duration mismatch')
    run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(exported), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    level = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(exported), '-vn',
                            '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True, timeout=120)
    match = re.search(r'max_volume: ([-\d.]+) dB', level.stderr)
    if level.returncode or not match or float(match.group(1)) < -60:
        raise EditError('Export audio is absent or effectively silent')
    events = []
    if (attempt / 'events.jsonl').exists():
        for line in (attempt / 'events.jsonl').read_text().splitlines():
            e = json.loads(line)
            if digest(local_file(e['evidence'])) != e['sha256']:
                raise EditError('GUI evidence changed')
            if e['event'] in {'export_requested', 'export_completed'} and Path(e['receipt']['export_path']).resolve() != exported:
                raise EditError('Export path differs from recorded GUI target')
            if e['event'] == 'project_reopened' and Path(e['receipt']['project_path']).resolve() != project:
                raise EditError('Native project differs from recorded reopened project')
            events.append(e)
    names = [e['event'] for e in events]
    if 'export_completed' in names and ('export_requested' not in names or names.index('export_requested') > names.index('export_completed')):
        raise EditError('Export completion has no preceding request')
    if len({e['receipt']['project_name'] for e in events}) > 1:
        raise EditError('GUI events refer to different projects')
    required = set(GUI_EVENTS)
    if not p['transitions']:
        required.remove('transitions_applied')
    missing = sorted(required - {e['event'] for e in events})
    latest = lambda name: max((i for i, e in enumerate(events) if e['event'] == name), default=-1)
    if latest('export_requested') > latest('export_completed'):
        missing.append('export_completed_for_latest_request')
    if latest('export_unknown') > latest('export_completed'):
        missing.append('export_outcome_reconciliation')
    if latest('needs_input') > latest('blocker_resolved'):
        missing.append('blocker_resolution')
    report = {'schema': 'jianying_edit_result/v1', 'plan_id': p['plan_id'],
              'status': 'verified_with_recorded_gui_review' if not missing else 'partial',
              'video': {'path': str(exported), 'sha256': digest(exported), 'media': m,
                        'full_decode_passed': True, 'audio_max_dbfs': float(match.group(1))},
              'project': {'path': str(project), 'files': {str(f.relative_to(project)): digest(f) for f in project_files}},
              'missing_gui_reviews': missing, 'gui_evidence': events,
              'boundary': 'GUI review is an operator attestation, not an automatic proof of subtitle/voice/transition semantics.'}
    write_json(attempt / 'verification.json', report)
    write_json(attempt / 'state.json', {'status': report['status'], 'plan_id': p['plan_id']})
    return report
