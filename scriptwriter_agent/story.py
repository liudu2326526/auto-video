"""Evidence-linked story planning, not media approval or semantic inference."""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from datetime import datetime


def need(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, required, optional=()):
    need(isinstance(value, dict), 'Story object required')
    need(set(required) <= value.keys() <= set(required) | set(optional),
         'Missing or unknown story fields')


def words(value):
    need(isinstance(value, str) and value.strip() and len(value) <= 12000,
         'Nonempty bounded story text required')


def integer(value, minimum=0):
    need(type(value) is int and minimum <= value <= 86_400_000,
         'Integer milliseconds required; bool/nonfinite/negative values forbidden')
    return value


def identity(value):
    need(isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value),
         'Invalid story identity')
    return value


def media_file(root, record):
    """Hash local bytes; refuse any symlink component, URL or directory escape."""
    need(root is not None, 'Evidence root required for media catalog')
    root = Path(root)
    need(root.is_dir() and not root.is_symlink(), 'Real evidence root required')
    root = root.resolve()
    ref = record['path']
    need(isinstance(ref, str) and ref and not Path(ref).is_absolute()
         and not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', ref), 'Relative local media path required')
    parts = Path(ref).parts
    need('..' not in parts, 'Media path escapes evidence root')
    path = root
    for part in parts:
        path = path / part
        need(not path.is_symlink(), 'Media symlink forbidden')
    need(path.is_file() and path.resolve().is_relative_to(root), 'Missing local media')
    need(isinstance(record['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', record['sha256']),
         'Media SHA-256 required')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    need(h.hexdigest() == record['sha256'], 'Media hash mismatch')
    return path


def media_duration_ms(path):
    """Probe only: never starts recording, renders frames or changes the source."""
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                            'stream=codec_type,duration', '-of', 'json', str(path)],
                           capture_output=True, text=True, timeout=15, check=True)
        metadata = json.loads(r.stdout)
        videos = [s for s in metadata['streams'] if s.get('codec_type') == 'video']
        need(len(videos) == 1, 'Exactly one unambiguous video stream required')
        # Container duration may include longer audio; it is not a visible-frame bound.
        seconds = float(videos[0]['duration'])
        need(math.isfinite(seconds) and 0 < seconds <= 86400, 'Invalid media duration')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise ValueError('Cannot probe local video evidence') from exc
    return math.floor(seconds * 1000)


def catalog_records(catalog, assets, tests, root, instant):
    fields(catalog, ['schema_version', 'media'])
    need(type(catalog['schema_version']) is int and catalog['schema_version'] == 1,
         'Unsupported media catalog schema')
    need(isinstance(catalog['media'], list) and len(catalog['media']) <= 32,
         'At most32 media records required')
    out, paths = {}, set()
    kinds = {'screen_result', 'context', 'tool_input', 'tool_action', 'tool_result',
             'tool_change', 'tool_verification'}
    for item in catalog['media']:
        fields(item, ['id', 'asset_id', 'path', 'sha256', 'reviewer', 'observed_at', 'segments'])
        mid = identity(item['id'])
        need(mid not in out, 'Duplicate media identity')
        need(item['asset_id'] in assets and assets[item['asset_id']]['status'] == 'available_unverified',
             'Catalog must reference an available_unverified asset; bytes do not approve rights')
        words(item['reviewer'])
        try:
            observed = datetime.fromisoformat(item['observed_at'].replace('Z', '+00:00'))
            need(observed.tzinfo is not None and observed <= instant,
                 'Future or naive media observation forbidden')
        except (TypeError, AttributeError) as exc:
            raise ValueError('Media observation timestamp required') from exc
        path = media_file(root, item)
        need(path not in paths, 'Duplicate media path; use one identity with multiple segments')
        paths.add(path)
        before = path.stat()
        duration = media_duration_ms(path)
        after = path.stat()
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
             'Media changed during inspection')
        need(isinstance(item['segments'], list) and 1 <= len(item['segments']) <= 128,
             '1..128 observed media segments required')
        segments = {}
        for segment in item['segments']:
            fields(segment, ['id', 'in_ms', 'out_ms', 'kind', 'observation', 'test_ids'])
            sid = identity(segment['id'])
            need(sid not in segments, 'Duplicate segment identity')
            need(integer(segment['in_ms']) < integer(segment['out_ms'], 1) <= duration,
                 'Observed segment outside actual media duration')
            need(segment['kind'] in kinds, 'Unknown observed segment kind')
            words(segment['observation'])
            tids = segment['test_ids']
            need(isinstance(tids, list) and all(isinstance(t, str) and t in tests for t in tids)
                 and len(tids) == len(set(tids)), 'Invalid media test references')
            if segment['kind'].startswith('tool_'):
                need(bool(tids), 'Tool operation/result evidence needs independent test observations')
            segments[sid] = segment
        out[mid] = {'record': item, 'duration_ms': duration, 'segments': segments}
    return out


def validate_story(draft, assets, tests, catalog, root, instant):
    """Every spoken/caption line is available with bounded evidence, or explicitly missing."""
    fields(draft, ['schema_version', 'tool_id', 'candidate_digest', 'brief', 'assets',
                   'scenes', 'recording_steps', 'story'])
    fields(draft['brief'], ['audience', 'pain_point', 'angle', 'hook', 'payoff', 'demo_input',
                            'success_criteria', 'duration_seconds', 'input_classification'])
    for asset in assets.values():
        fields(asset, ['id', 'description', 'origin', 'rights_review', 'privacy_review', 'status'])
    for step in draft['recording_steps']:
        fields(step, ['id', 'action', 'expected_evidence', 'stop_condition', 'recovery', 'scene_ids'])
    story = draft['story']
    fields(story, ['format', 'hook_alternatives', 'chosen_hook'])
    need(story['format'] in {'task_result', 'footage_preview'}, 'Unknown story format')
    hooks = story['hook_alternatives']
    need(isinstance(hooks, list) and 2 <= len(hooks) <= 3, '2..3 hook alternatives required')
    for hook in hooks:
        words(hook)
    need(len(set(hooks)) == len(hooks) and story['chosen_hook'] in hooks
         and story['chosen_hook'] == draft['brief']['hook'], 'Choose one exact brief hook')
    media = catalog_records(catalog, assets, tests, root, instant)
    beats = {'result', 'task', 'input', 'action', 'change', 'limitation', 'audience'}
    role = {'result': 'tool_result', 'input': 'tool_input', 'action': 'tool_action',
            'change': 'tool_change', 'limitation': 'tool_verification'}
    gaps, seen_lines, seen_beats, changes = [], set(), set(), []
    for scene in draft['scenes']:
        fields(scene, ['id', 'start', 'end', 'visual', 'narration', 'subtitle', 'purpose',
                       'asset_ids', 'source_ids', 'test_ids', 'claim_kind', 'beat', 'goal',
                       'hook', 'visible_change', 'emotion', 'transition_purpose', 'fallback', 'lines'],
               ['attribution'])
        need(scene['beat'] in beats, 'Unknown story beat')
        seen_beats.add(scene['beat'])
        for key in ('goal', 'hook', 'visible_change', 'emotion', 'transition_purpose', 'fallback'):
            words(scene[key])
        changes.append(scene['visible_change'])
        need(isinstance(scene['lines'], list) and 1 <= len(scene['lines']) <= 16,
             '1..16 line bindings per scene required')
        cursor = scene['start'] * 1000
        for line in scene['lines']:
            fields(line, ['id', 'start_ms', 'end_ms', 'narration', 'subtitle',
                          'status', 'evidence', 'missing'])
            lid = identity(line['id'])
            need(lid not in seen_lines, 'Duplicate line identity')
            seen_lines.add(lid)
            need(integer(line['start_ms']) == cursor < integer(line['end_ms'], 1)
                 <= scene['end'] * 1000, 'Line timing must exactly cover its scene')
            cursor = line['end_ms']
            words(line['narration']); words(line['subtitle'])
            need(line['status'] in {'available', 'missing'}, 'Unknown line material status')
            refs = line['evidence']
            need(isinstance(refs, list) and len(refs) <= 4, 'Bounded line evidence list required')
            if line['status'] == 'missing':
                need(not refs, 'Missing line cannot assert available evidence')
                fields(line['missing'], ['reason', 'required_shot', 'next_action'])
                for key in ('reason', 'required_shot'):
                    words(line['missing'][key])
                need(line['missing']['next_action'] in {'capture', 'transfer', 'rewrite', 'omit'},
                     'Explicit missing-material action required')
                gaps.append({'scene_id': scene['id'], 'line_id': lid, 'beat': scene['beat'],
                             **line['missing']})
                continue
            need(refs and line['missing'] is None, 'Available line requires evidence, no missing annotation')
            for ref in refs:
                fields(ref, ['media_id', 'segment_id', 'in_ms', 'out_ms'])
                need(ref['media_id'] in media, 'Dangling line media identity')
                item = media[ref['media_id']]
                need(item['record']['asset_id'] in scene['asset_ids'], 'Media asset not mapped to this scene')
                need(ref['segment_id'] in item['segments'], 'Dangling observed segment identity')
                segment = item['segments'][ref['segment_id']]
                need(segment['in_ms'] <= integer(ref['in_ms']) < integer(ref['out_ms'], 1)
                     <= segment['out_ms'], 'Line range outside observed media segment')
                if scene['beat'] in role and (story['format'] == 'task_result' or scene['beat'] != 'result'):
                    need(segment['kind'] == role[scene['beat']],
                         'Slide/context evidence cannot stand in for this tool-test beat')
                if scene['claim_kind'] == 'test_result':
                    need(bool(set(scene['test_ids']) & set(segment['test_ids'])),
                         'Test-result line lacks its actual test-linked media')
        need(cursor == scene['end'] * 1000, 'Line timing must exactly cover its scene')
        for field in ('narration', 'subtitle'):
            need(scene[field] == '\n'.join(line[field] for line in scene['lines']),
                 'Unmapped scene narration/subtitle text forbidden')
    need(draft['scenes'][0]['beat'] == 'result', 'Open with the real result or mark that result missing')
    if story['format'] == 'task_result':
        for beat in sorted(beats - seen_beats):
            gaps.append({'scene_id': None, 'line_id': None, 'beat': beat,
                         'reason': 'Required task/result story beat is not planned',
                         'required_shot': beat, 'next_action': 'rewrite'})
    warnings = []
    if len(set(changes)) < len(changes):
        warnings.append('Repeated visible-change descriptions: review for mechanical page narration.')
    if story['format'] == 'task_result' and not 45 <= draft['brief']['duration_seconds'] <= 55:
        warnings.append('45..55s is a pacing suggestion only; never pad footage to meet it.')
    return {'story': story, 'media_catalog': catalog,
            'media_checks': [{'id': k, 'duration_ms': v['duration_ms'], 'bytes_hash_matched': True}
                             for k, v in media.items()],
            'story_readiness': 'needs_materials' if gaps else 'evidence_linked_requires_review',
            'material_gaps': gaps, 'story_warnings': warnings}
