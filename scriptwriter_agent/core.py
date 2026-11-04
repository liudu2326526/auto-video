"""Validate a trusted writer's draft and export a reviewable production package.

Structural validation is not semantic fact checking or an approval mechanism.
All packages remain drafts requiring human review, even with valid test evidence.
"""
import hashlib
import html
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    require(isinstance(value, str), 'Timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'Timestamp timezone required')
    return result.astimezone(timezone.utc)


def text(value, field):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= 12000,
            f'{field}: nonempty text required (max 12000 characters)')
    return value


def indexed(items, label):
    require(isinstance(items, list), f'{label}: array required')
    out = {}
    for item in items:
        require(isinstance(item, dict), f'{label}: object required')
        key = item.get('id')
        require(isinstance(key, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', key),
                f'{label}: invalid ID')
        require(key not in out, f'{label}: duplicate ID {key}')
        out[key] = item
    return out


def references(values, mapping, label, required=False):
    require(isinstance(values, list) and all(isinstance(x, str) for x in values),
            f'{label}: reference array required')
    require(len(values) == len(set(values)), f'{label}: duplicate references')
    require(not required or bool(values), f'{label}: evidence required')
    require(all(x in mapping for x in values), f'{label}: dangling reference')


def safe_artifact(root, ref, expected_hash):
    require(isinstance(ref, str) and not Path(ref).is_absolute(), 'Relative artifact path required')
    path = (root / ref).resolve()
    require(path.is_relative_to(root.resolve()), 'Artifact escapes evidence root')
    require(path.is_file(), f'Missing evidence artifact: {ref}')
    require(isinstance(expected_hash, str) and re.fullmatch(r'[0-9a-f]{64}', expected_hash),
            'Evidence SHA-256 required')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash,
            f'Artifact hash mismatch: {ref}')


def build_package(candidate, draft, *, evaluated_at=None, test_report=None, evidence_root=None):
    """Inputs are local trusted annotations; source documents cannot grant authority."""
    evaluated_at = evaluated_at or datetime.now(timezone.utc).isoformat()
    instant = timestamp(evaluated_at)
    require(isinstance(candidate, dict) and isinstance(draft, dict), 'Objects required')
    require(type(draft.get('schema_version')) is int and draft['schema_version'] == 1,
            'Unsupported draft schema version')
    require(draft.get('tool_id') == candidate.get('tool_id') and bool(candidate.get('tool_id')),
            'Tool identity mismatch')
    require(draft.get('candidate_digest') == digest(candidate), 'Candidate changed; re-review source references')
    text(candidate.get('name'), 'candidate.name')
    brief = draft.get('brief', {})
    require(isinstance(brief, dict), 'Brief object required')
    for field in ('audience', 'pain_point', 'angle', 'hook', 'payoff', 'demo_input', 'success_criteria'):
        text(brief.get(field), 'brief.' + field)
    duration = brief.get('duration_seconds')
    require(type(duration) is int and 15 <= duration <= 180, 'Duration must be 15..180 integer seconds')
    require(brief.get('input_classification') == 'synthetic_nonpersonal',
            'MVP only accepts synthetic nonpersonal demo inputs')
    assessment = candidate.get('assessment', {})
    require(isinstance(assessment, dict), 'Assessment object required')
    facts = assessment.get('facts', [])
    require(isinstance(facts, list), 'Candidate facts array required')
    sources = {}
    warnings = []
    for i, fact in enumerate(facts, 1):
        require(isinstance(fact, dict), 'Fact object required')
        require(fact.get('fact_type') in ('observed', 'vendor_claim', 'third_party_report', 'untested'),
                'Unknown discovery fact type')
        citation = fact.get('citation') or {}
        require(isinstance(citation, dict), 'Citation object required')
        # A discovery "observed" fact is still a page observation, never a product test.
        sid = f'source_{i:03d}'
        url = citation.get('url', '')
        parsed = urlsplit(url)
        valid = (parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username
                 and not parsed.password and parsed.port in (None, 443)
                 and isinstance(citation.get('sha256'), str)
                 and bool(re.fullmatch(r'[0-9a-f]{64}', citation['sha256']))
                 and isinstance(citation.get('quote'), str) and bool(citation['quote'].strip()))
        try:
            fresh = timestamp(fact.get('expires_at')) > instant
        except (ValueError, TypeError):
            fresh = False
        sources[sid] = {'id': sid, 'kind': 'source_statement', 'fact': fact,
                        'citation_valid': bool(valid) and fact['fact_type'] != 'untested', 'fresh': fresh,
                        'capability_tested': False}
        if not valid or not fresh:
            warnings.append(f'{sid}: missing/expired source evidence; cannot support narration')
    tests = {}
    if test_report is not None:
        require(isinstance(test_report, dict) and type(test_report.get('schema_version')) is int
                and test_report['schema_version'] == 1,
                'Unsupported test report')
        require(test_report.get('tool_id') == candidate['tool_id'], 'Test tool mismatch')
        require(evidence_root is not None, 'Evidence root required for test reports')
        tests = indexed(test_report.get('observations'), 'test observations')
        for item in tests.values():
            require(item.get('status') in ('passed', 'failed'), 'Test observation must be completed')
            for field in ('claim', 'scenario', 'limitations', 'reviewer'):
                text(item.get(field), 'observation.' + field)
            require(timestamp(item.get('observed_at')) <= instant, 'Future observation forbidden')
            require(timestamp(item.get('expires_at')) > instant, 'Expired observation')
            for key in ('input_artifact', 'result_artifact'):
                artifact = item.get(key, {})
                require(isinstance(artifact, dict), 'Artifact object required')
                safe_artifact(Path(evidence_root), artifact.get('path'), artifact.get('sha256'))
    assets = indexed(draft.get('assets'), 'assets')
    require(bool(assets), 'At least one planned asset required')
    for asset in assets.values():
        for field in ('description', 'origin', 'rights_review', 'privacy_review'):
            text(asset.get(field), 'asset.' + field)
        require(asset.get('status') in ('to_capture', 'to_create', 'available_unverified'),
                'Assets are planned/unverified; this helper does not approve media')
    scenes = indexed(draft.get('scenes'), 'scenes')
    require(3 <= len(scenes) <= 16, '3..16 scenes required')
    cursor = 0
    used_sources, used_tests = set(), set()
    for scene in scenes.values():
        start, end = scene.get('start'), scene.get('end')
        require(type(start) is int and type(end) is int and start == cursor and end > start,
                'Scene timing must be contiguous integer seconds')
        cursor = end
        for field in ('visual', 'narration', 'subtitle', 'purpose'):
            text(scene.get(field), 'scene.' + field)
        kind = scene.get('claim_kind')
        surface = scene['narration'] + scene['subtitle']
        require(not re.search(r'永久免费|百分之百成功|100%成功|保证成功|一定成功', surface),
                'Absolute outcome promises forbidden')
        if kind not in ('test_result', 'source_claim'):
            require(not re.search(r'(亲测|实测发现|测试证明|我试了|只需.{0,6}[分钟秒]|快[0-9一二三四五六七八九十]+倍|[0-9]+分钟.{0,5}生成)', surface),
                    'Apparent result claim requires explicit evidence and review')
        require(kind in ('question', 'instruction', 'source_claim', 'test_result', 'opinion'),
                'Unknown claim kind')
        source_ids, test_ids = scene.get('source_ids', []), scene.get('test_ids', [])
        references(source_ids, sources, 'scene sources', kind == 'source_claim')
        references(test_ids, tests, 'scene tests', kind == 'test_result')
        references(scene.get('asset_ids'), assets, 'scene assets', True)
        require(kind == 'test_result' or not test_ids, 'Only test_result may cite test observations')
        for sid in source_ids:
            require(sources[sid]['citation_valid'] and sources[sid]['fresh'], 'Stale or invalid source used')
        if kind == 'source_claim':
            require(scene.get('attribution') == '来源声明，非本项目实测', 'Explicit source disclaimer required')
        if kind == 'test_result':
            require(scene.get('attribution') == '本次实测记录，非普遍效果保证', 'Explicit test disclaimer required')
        used_sources.update(source_ids)
        used_tests.update(test_ids)
    require(cursor == duration, 'Scene duration does not match brief')
    steps = indexed(draft.get('recording_steps'), 'recording steps')
    require(bool(steps), 'Recording steps required')
    covered = set()
    for step in steps.values():
        for field in ('action', 'expected_evidence', 'stop_condition', 'recovery'):
            text(step.get(field), 'recording step.' + field)
        references(step.get('scene_ids'), scenes, 'step scenes', True)
        covered.update(step['scene_ids'])
    require(covered == set(scenes), 'Recording plan must cover every scene')
    gates = assessment.get('gates', {})
    require(isinstance(gates, dict), 'Gates object required')
    blocked_gates = [key for key in ('HF1','HF2','HF3','HF4','HF5','HF6','HF7')
                     if not isinstance(gates.get(key), dict) or gates[key].get('status') != 'pass']
    blockers = ['Human semantic/fact review, rights/privacy review and actual media QA are required.',
                'No recording, payment, generation, or publication is authorized by this package.']
    if not used_tests:
        blockers.append('No test-backed result in script: verify the scenario before final narration.')
    if blocked_gates:
        blockers.append('Discovery gates require operator verification: ' + ', '.join(blocked_gates))
    narration_characters = sum(len(re.sub(r'\s', '', s['narration'])) for s in scenes.values())
    if narration_characters > duration * 5:
        warnings.append('Narration exceeds rough 5 characters/second budget; rehearse and shorten.')
    return {'schema_version': 1, 'status': 'draft_requires_human_review',
            'tool_id': candidate['tool_id'], 'tool_name': candidate['name'],
            'evaluated_at': evaluated_at,
            'input_digest': digest({'candidate': candidate, 'draft': draft, 'test_report': test_report}),
            'execution_authorized': False, 'publication_authorized': False,
            'brief': brief, 'sources': list(sources.values()), 'test_observations': list(tests.values()),
            'scenes': list(scenes.values()), 'assets': list(assets.values()),
            'recording_steps': list(steps.values()), 'blockers': blockers, 'warnings': warnings,
            'narration_characters': narration_characters,
            'review': {'decision': 'pending', 'reviewer': None,
                       'checks': ['逐句核对语义、事实类型和引用是否真正支持结论',
                                  '确认问句/建议没有暗含未经验证的结果承诺',
                                  '核对输入一致、真实耗时、失败情况与账号限制',
                                  '核对素材权利、个人信息遮挡、录屏授权',
                                  '读稿计时，完整看听原片，再冻结最终脚本']}}


def escaped(value):
    return re.sub(r'([\\`*_{}\[\]()#+!|>])', r'\\\1', html.escape(str(value)))


def render(package, section):
    lines = [f'# {escaped(package["tool_name"])} · {section}', '',
             '**拍摄草案，未经人工审核；不代表已实测、已录屏或可发布。**', '']
    if section == 'script':
        lines += [f'钩子：{escaped(package["brief"]["hook"])}',
                  f'观众收益：{escaped(package["brief"]["payoff"])}', '']
        for scene in package['scenes']:
            lines += [f'## {scene["id"]} · {scene["start"]}–{scene["end"]}秒',
                      escaped(scene['narration']), '',
                      '字幕：' + escaped(scene['subtitle']),
                      '断言类型：' + scene['claim_kind'],
                      '证据：' + ', '.join(scene.get('source_ids', []) + scene.get('test_ids', [])),
                      escaped(scene.get('attribution', '非实测结论')), '']
    elif section == 'storyboard':
        for scene in package['scenes']:
            lines += [f'## {scene["id"]} · {scene["start"]}–{scene["end"]}秒',
                      '画面：' + escaped(scene['visual']), '目的：' + escaped(scene['purpose']),
                      '素材：' + ', '.join(scene['asset_ids']), '']
    elif section == 'assets':
        for item in package['assets']:
            lines += [f'## {item["id"]}', *[f'{k}: {escaped(v)}' for k, v in item.items() if k != 'id'], '']
    elif section == 'recording-plan':
        lines += ['## 固定输入', escaped(package['brief']['demo_input']), '',
                  '## 成功标准', escaped(package['brief']['success_criteria']), '',
                  '先审查下列阻断项；步骤是待执行计划，不是执行权限。', '']
        for step in package['recording_steps']:
            lines += [f'## {step["id"]}', *[f'{k}: {escaped(v)}' for k, v in step.items() if k != 'id'], '']
    elif section == 'review':
        lines += ['## 阻断项', *['- ' + escaped(x) for x in package['blockers']],
                  '', '## 警告', *['- ' + escaped(x) for x in package['warnings']],
                  '', '## 人工检查', *['- [ ] ' + escaped(x) for x in package['review']['checks']],
                  '', '结论：pending；审核人：未填写。']
    else:
        raise ValueError('Unknown render section')
    return '\n'.join(lines) + '\n'


def write_package(package, destination):
    """Never overwrite an earlier take; refuse existing destination, even if empty."""
    destination = Path(destination)
    require(not destination.exists(), 'Output already exists; use a new attempt directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.scriptwriter-', dir=destination.parent))
    try:
        (staging / 'package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for section in ('script', 'storyboard', 'assets', 'recording-plan', 'review'):
            (staging / (section + '.md')).write_text(render(package, section), encoding='utf-8')
        manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(staging.iterdir())}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        require(not destination.exists(), 'Output already exists; use a new attempt directory')
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination
