"""A supervised reference comparison gate, never an aesthetic scoring model.

This sidecar does not drive Jianying, render a final video, license resources,
listen, or infer that an operator's semantic observation is true.
"""
import json
import os
import re
from pathlib import Path
from editing_agent.core import (EditError, digest, fields, identity, integer,
                                local_file, probe, read_json, validate as edit_validate)

GATES = {'same_content_comparison', 'numbers_source_result', 'error_and_real_correction',
         'phone_readability', 'rights_privacy', 'truthful_audio_picture'}


def words(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise EditError('Expected nonempty observation, at most 4000 characters')
    return value


def bound_file(obj, root):
    fields(obj, ['path', 'sha256'])
    words(obj['path'])
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', obj['path']):
        raise EditError('Evidence must be a local file')
    path = local_file(root / obj['path'])
    if not re.fullmatch('[0-9a-f]{64}', str(obj['sha256'])) or digest(path) != obj['sha256']:
        raise EditError('Evidence/file hash changed')
    return path


def evidence(rows, root):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        raise EditError('Expected 1..30 independently observed evidence files')
    return [bound_file(row, root) for row in rows]


def unique(rows, key):
    if not isinstance(rows, list) or len(rows) > 100:
        raise EditError('Expected bounded list')
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EditError('Expected object')
        k = identity(row.get(key))
        if k in found:
            raise EditError('Duplicate identity')
        found[k] = row
    return found


def validate(path):
    path = Path(path).resolve()
    p = read_json(path)
    fields(p, ['schema', 'id', 'edit_plan', 'references', 'shots', 'required_gates'])
    if p['schema'] != 'creative_review_plan/v1':
        raise EditError('Unsupported creative schema; no implicit legacy migration')
    identity(p['id'])
    base_path = bound_file(p['edit_plan'], path.parent)
    base, _ = edit_validate(base_path)
    if (not isinstance(p['required_gates'], list) or len(p['required_gates']) != len(GATES)
            or set(p['required_gates']) != GATES):
        raise EditError('All six hard gates are required exactly once')
    refs = unique(p['references'], 'id')
    if not refs:
        raise EditError('At least one actually observed reference required')
    for row in refs.values():
        fields(row, ['id', 'url', 'observation', 'evidence'])
        if not isinstance(row['url'], str) or not row['url'].startswith('https://'):
            raise EditError('Reference URL required')
        words(row['observation']); evidence(row['evidence'], path.parent)
    shots = unique(p['shots'], 'clip_id')
    clips = {c['id']: c for c in base['video']}
    if set(shots) != set(clips):
        raise EditError('Every actual edit clip requires one creative shot')
    accent_ids = set()
    source_metadata = {}
    for sid, row in shots.items():
        fields(row, ['clip_id', 'audience_question', 'one_focus', 'narration', 'caption',
                     'source_note', 'source_evidence', 'reference_ids', 'change', 'emotion', 'transition_purpose',
                     'missing_material_action', 'accents'])
        for key in ('audience_question', 'one_focus', 'source_note', 'change', 'emotion',
                    'transition_purpose', 'missing_material_action'):
            words(row[key])
        if not isinstance(row['source_evidence'], list) or not 1 <= len(row['source_evidence']) <= 6:
            raise EditError('Every shot needs bounded original-source evidence ranges')
        for source in row['source_evidence']:
            fields(source, ['file', 'in_frame', 'out_frame', 'fps', 'observation'])
            sp = bound_file(source['file'], path.parent)
            words(source['observation'])
            if type(source['fps']) is not int or source['fps'] != 30:
                raise EditError('Source evidence currently requires explicit integer CFR30')
            lo = integer(source['in_frame'], 0, 108000); hi = integer(source['out_frame'], 1, 108000)
            if str(sp) not in source_metadata:
                source_metadata[str(sp)] = probe(sp)
            vs = [v for v in source_metadata[str(sp)]['streams'] if v['codec_type'] == 'video']
            if (len(vs) != 1 or vs[0].get('avg_frame_rate') != '30/1'
                    or vs[0].get('r_frame_rate') != '30/1'
                    or not lo < hi <= int(vs[0].get('nb_read_frames', -1))):
                raise EditError('Source evidence range outside verified CFR30 media')
        cues = [c for c in base['subtitles'] if c['in_frame'] >= clips[sid]['timeline_in_frame']
                and c['out_frame'] <= clips[sid]['timeline_out_frame']]
        if row['caption'] != [c['text'] for c in cues]:
            raise EditError('Caption text must match frozen edit plan')
        if row['narration'] != [base['script']['lines'][c['line_id']] for c in cues]:
            raise EditError('Narration must match frozen script')
        if (not isinstance(row['reference_ids'], list) or not row['reference_ids']
                or len(set(row['reference_ids'])) != len(row['reference_ids'])
                or set(row['reference_ids']) - set(refs)):
            raise EditError('Unknown/duplicate reference')
        accents = unique(row['accents'], 'id')
        for aid, a in accents.items():
            fields(a, ['id', 'kind', 'purpose', 'resource', 'rights_basis',
                       'in_frame', 'out_frame', 'evidence_visibility'])
            if aid in accent_ids:
                raise EditError('Accent IDs must be globally unique')
            accent_ids.add(aid)
            if a['kind'] not in {'effect', 'sticker', 'sound', 'graphic', 'transition'}:
                raise EditError('Unsupported accent kind')
            for key in ('purpose', 'resource', 'rights_basis', 'evidence_visibility'):
                words(a[key])
            lo = integer(a['in_frame']); hi = integer(a['out_frame'])
            c = clips[sid]
            if not c['timeline_in_frame'] <= lo < hi <= c['timeline_out_frame']:
                raise EditError('Accent outside its shot')
    return p, base


def audit(plan_path, export_path, receipt_path):
    """Validate one export-bound operator review, preserving pending/rejected states."""
    pp = Path(plan_path).resolve(); p, base = validate(pp)
    ep = local_file(export_path); rp = Path(receipt_path).resolve(); r = read_json(rp)
    fields(r, ['schema', 'plan_sha256', 'export_sha256', 'reviewer', 'observed',
               'continuous_visual_review', 'audio_listened', 'shots', 'gates', 'resources'])
    if r['schema'] != 'creative_export_review/v1' or r['plan_sha256'] != digest(pp) or r['export_sha256'] != digest(ep):
        raise EditError('Review not bound to this exact plan/export')
    if r['reviewer'] not in {'operator', 'user'}:
        raise EditError('Review must attribute an actual operator or user')
    words(r['observed'])
    for key in ('continuous_visual_review', 'audio_listened'):
        if type(r[key]) is not bool:
            raise EditError('Actual review status must be a boolean')
    media = probe(ep); videos = [s for s in media['streams'] if s['codec_type'] == 'video']
    audios = [s for s in media['streams'] if s['codec_type'] == 'audio']
    o = base['output']
    if len(videos) != 1 or len(audios) != 1:
        raise EditError('Expected one video and one final mixed audio track')
    v = videos[0]
    if (v['width'], v['height'], v.get('avg_frame_rate'), v.get('r_frame_rate'),
            int(v.get('nb_read_frames', -1))) != (o['width'], o['height'], '30/1', '30/1', o['frames']):
        raise EditError('Export dimensions/fps/frame count differ from frozen plan')
    blockers = []
    if not r['continuous_visual_review']: blockers.append('continuous_visual_review_pending')
    if not r['audio_listened']: blockers.append('actual_listening_pending')
    shots = unique(r['shots'], 'clip_id')
    if set(shots) != {s['clip_id'] for s in p['shots']}:
        raise EditError('Every shot needs a comparison receipt')
    for sid, shot in shots.items():
        fields(shot, ['clip_id', 'verdict', 'reference_comparison', 'observed_gap',
                      'revision_result', 'evidence'])
        for k in ('reference_comparison', 'observed_gap', 'revision_result'): words(shot[k])
        evidence(shot['evidence'], rp.parent)
        if shot['verdict'] not in {'matched', 'needs_revision', 'unreviewed'}:
            raise EditError('Unknown shot verdict')
        if shot['verdict'] != 'matched': blockers.append('shot:' + sid + ':' + shot['verdict'])
    gates = unique(r['gates'], 'id')
    if set(gates) != GATES: raise EditError('Hard gates cannot be omitted')
    for gid, gate in gates.items():
        fields(gate, ['id', 'verdict', 'observation', 'evidence'])
        words(gate['observation']); evidence(gate['evidence'], rp.parent)
        if gate['verdict'] not in {'passed', 'failed', 'pending'}:
            raise EditError('Unknown hard gate status')
        if gate['verdict'] != 'passed': blockers.append('gate:' + gid + ':' + gate['verdict'])
    required = {a['id'] for s in p['shots'] for a in s['accents']}
    resources = unique(r['resources'], 'id')
    if set(resources) != required: raise EditError('All planned accents need actual application receipts')
    for aid, a in resources.items():
        fields(a, ['id', 'status', 'observed_resource', 'rights_observation', 'evidence'])
        words(a['observed_resource']); words(a['rights_observation']); evidence(a['evidence'], rp.parent)
        if a['status'] not in {'applied', 'missing', 'uncleared'}: raise EditError('Unknown resource status')
        if a['status'] != 'applied': blockers.append('resource:' + aid + ':' + a['status'])
    rejected = any(':failed' in b or ':needs_revision' in b for b in blockers)
    return {'schema': 'creative_audit/v1', 'status': 'needs_revision' if rejected else 'needs_review' if blockers else 'review_complete',
            'plan_sha256': digest(pp), 'export_sha256': digest(ep), 'review_sha256': digest(rp),
            'blockers': sorted(blockers), 'automatic_aesthetic_judgment': False,
            'observation_scope': 'Operator assertions validated structurally; no semantic/audio/rights proof or publication approval.'}


def save_new(path, result):
    # Never overwrite a rejected review or older iteration, even through a symlink.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
