"""Offline material bookkeeping; no browser, downloader, model or publication API.

Records/reviews are operator assertions, not legal or semantic certification.
Content is deduplicated independently of immutable provenance records.
"""
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit


class MaterialError(ValueError):
    pass


def need(value, message):
    if not value:
        raise MaterialError(message)


def fields(obj, required, optional=()):
    need(isinstance(obj, dict) and set(required) <= obj.keys()
         and obj.keys() <= set(required) | set(optional), 'Missing/unknown fields: ' + ', '.join(required))


def text(value, maximum=12000):
    need(isinstance(value, str) and value.strip() and len(value) <= maximum
         and not any(ord(c) < 32 and c not in '\n\t' for c in value), 'Nonempty bounded text required')
    return value


def identity(value):
    need(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value), 'Invalid identity')
    return value


def strings(value, maximum=64, empty=True):
    need(isinstance(value, list) and len(value) <= maximum and (empty or value), 'Bounded text array required')
    for item in value:
        text(item, 2000)
    need(len(value) == len(set(value)), 'Duplicate text array entries')
    return value


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        need(result.tzinfo is not None, 'Timezone required')
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise MaterialError('Invalid timezone-aware timestamp') from exc


def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat().replace('+00:00', 'Z')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(MaterialError('Nonfinite JSON forbidden')))


def url(value):
    text(value, 4000)
    try:
        parts = urlsplit(value)
        need(parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
             and parts.port in (None, 443) and not any(c.isspace() for c in value)
             and '\\' not in value, 'Public credential-free HTTPS URL required')
    except ValueError as exc:
        raise MaterialError('Invalid source URL') from exc
    return value


def location(path):
    path = Path(path).absolute()
    need('..' not in path.parts, 'Parent path traversal forbidden')
    # macOS exposes /var and /tmp as OS-owned aliases; user symlinks stay forbidden.
    if sys.platform == 'darwin':
        for alias in ('/var', '/tmp'):
            anchor = Path(alias)
            if path.is_relative_to(anchor) and anchor.is_symlink() and anchor.resolve() == Path('/private' + alias):
                path = Path('/private' + alias) / path.relative_to(anchor)
    need(not any(p.is_symlink() for p in [path, *path.parents]), 'Symlink path forbidden')
    return path


def runtime_location(path):
    path = location(path)
    repo = Path(__file__).resolve().parents[1]
    need(not path.is_relative_to(repo) or path.is_relative_to(repo / 'var'),
         'Runtime library/handoff inside repository must be under ignored var/')
    return path


def regular(path):
    path = location(path)
    need(path.is_file() and 0 < path.stat().st_size <= 2 * 1024 ** 3, 'Nonempty file up to 2 GiB required')
    return path


def context(value):
    fields(value, ['commercial', 'derivatives', 'distribution', 'role', 'share_alike_compatible'])
    need(all(type(value[k]) is bool for k in ('commercial', 'derivatives', 'share_alike_compatible')),
         'Boolean use flags required')
    need(value['distribution'] in {'internal', 'public_social', 'paid_advertising'}, 'Unknown distribution')
    need(value['role'] == 'illustrative_context', 'Stock material cannot serve as product-test evidence')
    return value


def validate_record(record, at=None):
    at = at or now()
    fields(record, ['schema', 'id', 'title', 'description', 'media_type', 'tags', 'source', 'rights', 'risks'])
    need(record['schema'] == 'material_record/v1', 'Unknown material schema')
    identity(record['id'])
    text(record['title'], 500)
    text(record['description'])
    need(record['media_type'] in {'image', 'video', 'audio'}, 'Unsupported media type')
    strings(record['tags'])
    strings(record['risks'])
    source = record['source']
    fields(source, ['provider', 'page_url', 'creator', 'retrieved_at', 'access', 'download_url'])
    text(source['provider'], 500)
    text(source['creator'], 1000)
    if source['page_url'] is not None:
        url(source['page_url'])
    if source['download_url'] is not None:
        url(source['download_url'])
    need(source['access'] in {'public_no_login', 'unknown_local_history'}, 'Unsupported source access')
    if source['access'] == 'public_no_login':
        need(source['page_url'] is not None, 'Public research requires a source page')
    else:
        need(source['page_url'] is None and source['download_url'] is None, 'Unknown history must not invent source URLs')
    need(instant(source['retrieved_at']) <= at, 'Future source retrieval forbidden')
    rights = record['rights']
    fields(rights, ['status', 'license_id', 'license_url', 'commercial_use', 'derivatives',
                    'attribution_required', 'attribution_text', 'restrictions', 'checked_at', 'evidence',
                    'allowed_distributions', 'valid_until', 'share_alike_required'])
    need(rights['status'] in {'known', 'unknown', 'restricted'}, 'Unknown rights status')
    if source['access'] == 'unknown_local_history':
        need(rights['status'] == 'unknown', 'Unknown historical source cannot grant known rights')
    text(rights['license_id'], 200)
    if rights['license_url'] is not None:
        url(rights['license_url'])
    for key in ('commercial_use', 'derivatives'):
        need(rights[key] in {'allowed', 'prohibited', 'unknown'}, 'Explicit rights flag required')
    need(rights['attribution_required'] is None or type(rights['attribution_required']) is bool,
         'Explicit or unknown attribution flag required')
    need(rights['share_alike_required'] is None or type(rights['share_alike_required']) is bool,
         'Explicit or unknown share-alike flag required')
    if rights['allowed_distributions'] is not None:
        strings(rights['allowed_distributions'], 3, empty=False)
        need(set(rights['allowed_distributions']) <= {'internal', 'public_social', 'paid_advertising'},
             'Unknown license distribution scope')
    if rights['valid_until'] is not None:
        instant(rights['valid_until'])
    need(isinstance(rights['attribution_text'], str) and len(rights['attribution_text']) <= 4000,
         'Attribution text required (may be empty only if not required)')
    if rights['attribution_required'] is True:
        text(rights['attribution_text'])
    strings(rights['restrictions'])
    if rights['checked_at'] is not None:
        need(instant(rights['checked_at']) <= at, 'Future license check forbidden')
    need(isinstance(rights['evidence'], list) and len(rights['evidence']) <= 8, 'At most 8 license evidence records')
    for evidence in rights['evidence']:
        fields(evidence, ['url', 'retrieved_at', 'scope', 'excerpt'])
        url(evidence['url'])
        need(instant(evidence['retrieved_at']) <= at, 'Future license evidence forbidden')
        need(evidence['scope'] in {'asset_license', 'license_terms'}, 'License evidence scope required')
        text(evidence['excerpt'], 4000)
    if rights['status'] == 'known':
        need(rights['license_url'] is not None and rights['checked_at'] is not None
             and {e['scope'] for e in rights['evidence']} >= {'asset_license', 'license_terms'},
             'Known rights require asset-specific evidence and license terms')
        need(all(instant(e['retrieved_at']) <= instant(rights['checked_at']) for e in rights['evidence']),
             'License verification cannot predate its evidence retrieval')
    return record


def validate_requests(value, script=None):
    fields(value, ['schema', 'project_id', 'public_nonpersonal', 'use_context', 'requests'])
    need(value['schema'] == 'material_requests/v1' and value['public_nonpersonal'] is True,
         'Explicit public, nonpersonal queries required; never search raw/private briefs')
    identity(value['project_id'])
    context(value['use_context'])
    need(isinstance(value['requests'], list) and 1 <= len(value['requests']) <= 64, '1..64 material requests required')
    ids, assets = set(), set()
    scenes = {s['id']: s for s in script['scenes']} if script else None
    for row in value['requests']:
        fields(row, ['id', 'scene_id', 'line_ids', 'asset_id', 'description', 'public_queries', 'media_types', 'tags', 'role'])
        for key in ('id', 'scene_id', 'asset_id'):
            identity(row[key])
        need(row['id'] not in ids and row['asset_id'] not in assets, 'Duplicate request or target asset ID')
        ids.add(row['id'])
        assets.add(row['asset_id'])
        strings(row['line_ids'], 32)
        for line in row['line_ids']:
            identity(line)
        text(row['description'])
        strings(row['public_queries'], 6, empty=False)
        strings(row['media_types'], 3, empty=False)
        need(set(row['media_types']) <= {'image', 'video', 'audio'}, 'Unknown requested media type')
        strings(row['tags'])
        need(row['role'] == 'illustrative_context', 'Web material only illustrates context')
        if scenes is not None:
            need(row['scene_id'] in scenes, 'Unknown script scene')
            scene = scenes[row['scene_id']]
            need(row['asset_id'] in scene['asset_ids'], 'Target asset is not in script scene')
            if row['line_ids']:
                known = {line['id'] for line in scene.get('lines', [])}
                need(set(row['line_ids']) <= known, 'Unknown script line')
    return value


class Library:
    """Single-operator local SQLite store. No concurrent writer service is promised."""
    def __init__(self, root='var/material-library'):
        self.root = runtime_location(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ('blobs', 'library.sqlite3'):
            need(not (self.root / name).is_symlink(), 'Library component symlink forbidden')
        self.db = sqlite3.connect(self.root / 'library.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS blobs (
                sha256 TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, size INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, document TEXT NOT NULL, digest TEXT NOT NULL,
                blob_sha256 TEXT REFERENCES blobs(sha256), imported_at TEXT,
                download_record TEXT, added_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (
                seq INTEGER PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id),
                context_digest TEXT NOT NULL, receipt TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usages (
                id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id),
                receipt TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handoffs (
                id TEXT PRIMARY KEY, document TEXT NOT NULL, created_at TEXT NOT NULL);
        ''')
        for table in ('blobs', 'records', 'reviews', 'usages', 'handoffs'):
            for action in ('UPDATE', 'DELETE'):
                self.db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} '
                                "BEGIN SELECT RAISE(ABORT, 'append-only material record'); END")

    def close(self):
        self.db.close()

    def get(self, record_id):
        identity(record_id)
        row = self.db.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
        need(row is not None, 'Unknown material record')
        out = dict(row)
        out['document'] = read_value = json.loads(out['document'])
        need(digest(read_value) == out['digest'], 'Stored metadata digest mismatch')
        return out

    def _blob(self, sha):
        row = self.db.execute('SELECT * FROM blobs WHERE sha256=?', (sha,)).fetchone()
        need(row is not None, 'Missing local blob')
        path = regular(self.root / row['path'])
        need(path.is_relative_to(self.root / 'blobs') and file_hash(path) == sha, 'Local blob changed')
        return path

    def add(self, record, file=None, download=None, at=None):
        at = at or now()
        validate_record(record, at)
        # A repeated ID can never refresh timestamps/permissions or replace bytes.
        need(self.db.execute('SELECT 1 FROM records WHERE id=?', (record['id'],)).fetchone() is None,
             'Immutable record exists; use a new ID for a new source/license verification')
        sha, imported, download_record = None, None, None
        if file is not None:
            fields(download, ['downloaded_at', 'method', 'source_url'])
            need(instant(download['downloaded_at']) <= at, 'Future download forbidden')
            need(download['method'] in {'manual_public_download', 'previous_local_material'}, 'Unsupported acquisition method')
            if download['source_url'] is None:
                need(download['method'] == 'previous_local_material' and record['source']['access'] == 'unknown_local_history',
                     'Only unknown previous local history may omit its acquisition URL')
            else:
                url(download['source_url'])
                need(record['source']['access'] == 'public_no_login', 'Unknown history cannot claim a public download')
            need(download['source_url'] in {record['source']['page_url'], record['source']['download_url']},
                 'Download record must match this source')
            source = regular(file)
            need(source.suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.mov', '.webm', '.wav', '.mp3', '.m4a'},
                 'Unsupported media extension; import media, not archives or executable files')
            sha = file_hash(source)
            existing = self.db.execute('SELECT * FROM blobs WHERE sha256=?', (sha,)).fetchone()
            if existing:
                self._blob(sha)
            else:
                directory = self.root / 'blobs' / sha[:2]
                need(not directory.is_symlink(), 'Blob directory symlink forbidden')
                directory.mkdir(exist_ok=True, parents=True)
                target = directory / (sha + source.suffix.lower())
                need(not target.exists() and not target.is_symlink(), 'Untracked blob collision')
                try:
                    with target.open('xb') as output, source.open('rb') as stream:
                        shutil.copyfileobj(stream, output)
                    need(file_hash(target) == sha, 'Source changed during import')
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                self.db.execute('INSERT INTO blobs VALUES (?,?,?)',
                                (sha, str(target.relative_to(self.root)), target.stat().st_size))
            imported, download_record = stamp(at), canonical(download)
        else:
            need(download is None, 'Cannot record a download without local bytes')
        with self.db:
            self.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?)',
                            (record['id'], canonical(record), digest(record), sha, imported, download_record, stamp(at)))
        return self.get(record['id'])

    def review(self, receipt, at=None):
        at = at or now()
        fields(receipt, ['schema', 'record_id', 'record_digest', 'file_sha256', 'reviewer', 'reviewed_at',
                         'expires_at', 'use_context', 'decision', 'conditions_acknowledged', 'risk_review',
                         'visual_review', 'reviewer_kind'])
        need(receipt['schema'] == 'material_review/v1', 'Unknown review schema')
        row = self.get(receipt['record_id'])
        need(receipt['record_digest'] == row['digest'] and row['blob_sha256'] is not None
             and receipt['file_sha256'] == row['blob_sha256'], 'Review must bind exact metadata and local bytes')
        self._blob(row['blob_sha256'])
        context(receipt['use_context'])
        text(receipt['reviewer'], 500)
        text(receipt['risk_review'])
        text(receipt['visual_review'])
        checked = instant(receipt['reviewed_at'])
        expires = instant(receipt['expires_at'])
        need(checked <= at < expires <= checked + timedelta(days=30), 'Review must be current and expire within 30 days')
        acquired = json.loads(row['download_record'])
        need(checked >= max(instant(row['added_at']), instant(row['imported_at']), instant(acquired['downloaded_at'])),
             'Review cannot predate metadata registration or acquisition/import of local bytes')
        need(receipt['decision'] in {'approved', 'rejected'}, 'Explicit review decision required')
        need(receipt['reviewer_kind'] in {'human', 'agent'}, 'Explicit reviewer kind required')
        strings(receipt['conditions_acknowledged'])
        if receipt['decision'] == 'approved':
            need(receipt['reviewer_kind'] == 'human', 'Approved requires an actual human review; agent notes are not clearance')
            rights = row['document']['rights']
            need(set(receipt['conditions_acknowledged']) == set(rights['restrictions']), 'Acknowledge every license restriction')
            issues = self._rights_issues(row['document'], receipt['use_context'], at)
            need(not issues, '; '.join(issues))
            need(instant(rights['checked_at']) <= checked and expires <= instant(rights['checked_at']) + timedelta(days=30),
                 'Review must follow license verification and cannot extend its freshness')
            need(rights['valid_until'] is None or expires <= instant(rights['valid_until']),
                 'Review cannot extend beyond license expiry')
        previous = self.db.execute('SELECT receipt FROM reviews WHERE record_id=? AND context_digest=? ORDER BY seq DESC LIMIT 1',
                                   (row['id'], digest(receipt['use_context']))).fetchone()
        if previous:
            previous = json.loads(previous['receipt'])
            if canonical(previous) == canonical(receipt):
                return receipt  # Idempotence must not append/promote an old review.
            prior_time = instant(previous['reviewed_at'])
            need(checked > prior_time or (checked == prior_time and receipt['decision'] == 'rejected'
                                         and previous['decision'] == 'approved'),
                 'Old approval/review replay forbidden; a new approval must follow the latest review in time')
        with self.db:
            self.db.execute('INSERT INTO reviews(record_id,context_digest,receipt) VALUES (?,?,?)',
                            (row['id'], digest(receipt['use_context']), canonical(receipt)))
        return receipt

    @staticmethod
    def _rights_issues(record, use, at):
        rights, issues = record['rights'], []
        if rights['status'] != 'known':
            issues.append('Unknown/restricted license requires new verification')
        if rights['checked_at'] is None or not at - timedelta(days=30) <= instant(rights['checked_at']) <= at:
            issues.append('License verification missing/older than 30 days')
        if use['commercial'] and rights['commercial_use'] != 'allowed':
            issues.append('Commercial use not explicitly allowed')
        if use['derivatives'] and rights['derivatives'] != 'allowed':
            issues.append('Derivatives not explicitly allowed')
        if rights['attribution_required'] is None:
            issues.append('Attribution requirement unknown')
        if rights['allowed_distributions'] is None or use['distribution'] not in rights['allowed_distributions']:
            issues.append('Distribution not explicitly licensed')
        if rights['valid_until'] is not None and instant(rights['valid_until']) <= at:
            issues.append('License expired')
        if rights['share_alike_required'] is None:
            issues.append('Share-alike obligations unknown')
        elif rights['share_alike_required'] and use['derivatives'] and not use['share_alike_compatible']:
            issues.append('Share-alike handling not confirmed for this use')
        return issues

    def eligibility(self, record_id, use, at=None):
        at = at or now()
        context(use)
        row = self.get(record_id)
        issues = self._rights_issues(row['document'], use, at)
        if at < instant(row['added_at']) or (row['imported_at'] and at < instant(row['imported_at'])):
            issues.append('Evaluation predates material registration/import')
        path = None
        try:
            need(row['blob_sha256'] is not None, 'Candidate has no local file; download/import required')
            path = self._blob(row['blob_sha256'])
        except (MaterialError, OSError) as exc:
            issues.append(str(exc))
        review = self.db.execute('SELECT receipt FROM reviews WHERE record_id=? AND context_digest=? ORDER BY seq DESC LIMIT 1',
                                 (record_id, digest(use))).fetchone()
        if review is None:
            issues.append('Human review pending for this exact use context')
        else:
            review = json.loads(review['receipt'])
            if review['decision'] != 'approved':
                issues.append('Latest review rejected this use context')
            if not instant(review['reviewed_at']) <= at < instant(review['expires_at']):
                issues.append('Human review expired/not yet valid')
            if review['record_digest'] != row['digest'] or review['file_sha256'] != row['blob_sha256']:
                issues.append('Review no longer matches metadata/bytes')
        return {'record_id': record_id, 'eligible': not issues, 'blockers': issues,
                'path': str(path) if path else None, 'sha256': row['blob_sha256'],
                'record_digest': row['digest'], 'review': review}

    def search(self, query, use, media_types=None, limit=10, at=None):
        text(query, 2000)
        context(use)
        need(media_types is None or isinstance(media_types, list) and bool(media_types)
             and all(t in {'image', 'video', 'audio'} for t in media_types), 'Invalid media filters')
        need(type(limit) is int and 1 <= limit <= 100, 'Search limit must be 1..100')
        terms = query.casefold().split()
        hits = []
        for stored in self.db.execute('SELECT id,document FROM records ORDER BY id'):
            record = json.loads(stored['document'])
            if media_types and record['media_type'] not in media_types:
                continue
            haystack = ' '.join([record['title'], record['description'], *record['tags']]).casefold()
            score = sum(1 for term in terms if term in haystack)
            if score:
                hits.append({'record_id': record['id'], 'title': record['title'], 'media_type': record['media_type'],
                             'tags': record['tags'], 'score': score, **self.eligibility(record['id'], use, at)})
        return sorted(hits, key=lambda h: (-h['score'], h['record_id']))[:limit]

    def plan(self, requests, script=None, at=None):
        validate_requests(requests, script)
        rows = []
        for row in requests['requests']:
            query = ' '.join([*row['tags'], *row['public_queries']])
            rows.append({**row, 'local_matches': self.search(query, requests['use_context'], row['media_types'], at=at),
                         'web_search_status': 'operator_required',
                         'instructions': 'Use only explicit public_queries; inspect asset-specific license and terms. Stop on login/paywall/access denial.'})
        return {'schema': 'material_research_plan/v1', 'project_id': requests['project_id'],
                'requests_digest': digest(requests), 'created_at': stamp(at), 'requests': rows,
                'automatic_web_search': False, 'automatic_download': False}

    def handoff(self, requests, selection, output, at=None):
        at = at or now()
        validate_requests(requests)
        fields(selection, ['schema', 'requests_digest', 'selections'])
        need(selection['schema'] == 'material_selection/v1' and selection['requests_digest'] == digest(requests),
             'Selection must bind exact material requests')
        need(isinstance(selection['selections'], list) and 1 <= len(selection['selections']) <= 64, 'Selections required')
        mapping, selected, seen = {r['id']: r for r in requests['requests']}, [], set()
        for choice in selection['selections']:
            fields(choice, ['request_id', 'record_id', 'operator_notes'])
            need(choice['request_id'] in mapping and choice['request_id'] not in seen, 'Unknown/duplicate request selection')
            seen.add(choice['request_id'])
            text(choice['operator_notes'])
            request = mapping[choice['request_id']]
            row = self.get(choice['record_id'])
            need(row['document']['media_type'] in request['media_types'], 'Selected material has wrong media type')
            check = self.eligibility(choice['record_id'], requests['use_context'], at)
            need(check['eligible'], '; '.join(check['blockers']))
            selected.append((request, row, check, choice['operator_notes']))
        output = runtime_location(output)
        need(not output.exists(), 'New nonsymlink handoff directory required')
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.materials-', dir=output.parent))
        reserved = False
        try:
            (staging / 'media').mkdir()
            assets, editor, items = [], {}, []
            for request, row, check, notes in selected:
                record = row['document']
                source = Path(check['path'])
                relative = 'media/' + request['asset_id'] + source.suffix
                target = staging / relative
                shutil.copyfile(source, target)
                need(file_hash(target) == check['sha256'], 'Blob changed during handoff')
                basis = record['rights']['license_id'] + '; source=' + record['source']['page_url']
                assets.append({'id': request['asset_id'], 'description': record['description'],
                               'origin': record['source']['page_url'], 'rights_review': basis + '; see materials.json exact review',
                               'privacy_review': check['review']['risk_review'], 'status': 'available_unverified'})
                compatible = _editor_video(target) if record['media_type'] == 'video' else False
                if compatible:
                    editor[request['asset_id']] = {'path': relative, 'sha256': check['sha256'],
                                                   'rights': 'cleared', 'rights_basis': basis + '; materials.json contains review and attribution'}
                items.append({'request_id': request['id'], 'scene_id': request['scene_id'], 'line_ids': request['line_ids'],
                              'asset_id': request['asset_id'], 'record_id': row['id'], 'record_digest': row['digest'],
                              'path': relative, 'sha256': check['sha256'], 'source': record['source'],
                              'rights': record['rights'], 'review': check['review'], 'risks': record['risks'],
                              'operator_notes': notes, 'role': 'illustrative_context', 'product_test_evidence': False,
                              'editor_video_candidate': compatible, 'requires_media_preparation': not compatible})
            result = {'schema': 'material_handoff/v1', 'project_id': requests['project_id'], 'created_at': stamp(at),
                      'requests_digest': digest(requests), 'use_context': requests['use_context'], 'materials': items,
                      'unfilled_request_ids': sorted(set(mapping) - seen),
                      'status': 'operator_reviewed_materials_not_final_edit',
                      'editor_note': 'Merge editor-assets.json into a new jianying_edit_plan/v1 and validate with editing_agent; no full edit plan or media conversion is performed.'}
            for name, obj in [('writer-assets.json', assets), ('editor-assets.json', editor)]:
                (staging / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            (staging / 'attribution.txt').write_text('\n\n'.join(
                f"{r['document']['title']} — {r['document']['source']['creator']}\n{r['document']['source']['page_url']}\n"
                f"{r['document']['rights']['license_url']}\n{r['document']['rights']['attribution_text']}"
                for _, r, _, _ in selected) + '\n', encoding='utf-8')
            result['files'] = {str(p.relative_to(staging)): file_hash(p) for p in staging.rglob('*') if p.is_file()}
            result['handoff_id'] = 'handoff_' + digest(result)[:24]
            (staging / 'materials.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            # mkdir reserves the final name without rename's replace-empty-dir behavior.
            output.mkdir(exist_ok=False)
            reserved = True
            for child in staging.iterdir():
                os.rename(child, output / child.name)
            staging.rmdir()
            with self.db:
                self.db.execute('INSERT INTO handoffs VALUES (?,?,?)', (result['handoff_id'], canonical(result), stamp(at)))
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            if reserved:
                shutil.rmtree(output, ignore_errors=True)
            raise
        return result

    def verify_handoff(self, path, at=None):
        """Run before editing: neither copying a handoff nor yesterday's review grants new use."""
        at = at or now()
        path = regular(path)
        manifest = read_json(path)
        need(manifest.get('schema') == 'material_handoff/v1', 'Unknown handoff schema')
        stored = self.db.execute('SELECT document FROM handoffs WHERE id=?', (manifest.get('handoff_id'),)).fetchone()
        need(stored is not None and canonical(manifest) == stored['document'], 'Unregistered/modified material handoff')
        for relative, sha in manifest['files'].items():
            need(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'Invalid handoff file reference')
            file = regular(path.parent / relative)
            need(file.is_relative_to(path.parent) and file_hash(file) == sha, 'Handoff file missing/changed: ' + relative)
        for item in manifest['materials']:
            check = self.eligibility(item['record_id'], manifest['use_context'], at)
            need(check['eligible'], '; '.join(check['blockers']))
            need(check['record_digest'] == item['record_digest'] and check['sha256'] == item['sha256'],
                 'Handoff no longer matches library metadata/bytes')
            need(canonical(check['review']) == canonical(item['review']), 'Handoff review superseded; create a new handoff')
        return {'schema': 'material_handoff_check/v1', 'handoff_id': manifest['handoff_id'],
                'checked_at': stamp(at), 'eligible': True, 'final_edit_verified': False}

    def record_usage(self, receipt, at=None):
        at = at or now()
        fields(receipt, ['schema', 'id', 'record_id', 'record_digest', 'file_sha256', 'project_id', 'scene_id',
                         'line_ids', 'use_context', 'used_at', 'operator', 'edit_reference'])
        need(receipt['schema'] == 'material_usage/v1', 'Unknown usage schema')
        for key in ('id', 'record_id', 'project_id', 'scene_id'):
            identity(receipt[key])
        strings(receipt['line_ids'], 32)
        text(receipt['operator'], 500)
        text(receipt['edit_reference'])
        used = instant(receipt['used_at'])
        need(used <= at, 'Cannot log future use')
        check = self.eligibility(receipt['record_id'], receipt['use_context'], at)
        need(check['eligible'], '; '.join(check['blockers']))
        need(receipt['file_sha256'] == check['sha256'] and receipt['record_digest'] == check['record_digest'], 'Usage hash mismatch')
        need(instant(check['review']['reviewed_at']) <= used < instant(check['review']['expires_at']),
             'Usage date outside reviewed interval')
        with self.db:
            self.db.execute('INSERT INTO usages VALUES (?,?,?)', (receipt['id'], receipt['record_id'], canonical(receipt)))
        return receipt

    def history(self, record_id):
        row = self.get(record_id)
        return {**row, 'download_record': json.loads(row['download_record']) if row['download_record'] else None,
                'reviews': [json.loads(r[0]) for r in self.db.execute('SELECT receipt FROM reviews WHERE record_id=? ORDER BY seq', (record_id,))],
                'usages': [json.loads(r[0]) for r in self.db.execute('SELECT receipt FROM usages WHERE record_id=? ORDER BY id', (record_id,))]}


def _editor_video(path):
    """Only a format precheck; editing_agent remains responsible for the full plan."""
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-show_format',
                                 '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30)
        value = json.loads(result.stdout)
        video = [s for s in value['streams'] if s['codec_type'] == 'video']
        if len(video) != 1 or not 0 < int(video[0]['nb_read_frames']) <= 18000:
            return False
        if not video[0].get('avg_frame_rate') == video[0].get('r_frame_rate') == '30/1':
            return False
        frames = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                 'frame=best_effort_timestamp_time', '-of', 'json', str(path)],
                                capture_output=True, text=True, check=True, timeout=30)
        pts = [float(f['best_effort_timestamp_time']) for f in json.loads(frames.stdout)['frames']]
        tick = float(Fraction(video[0]['time_base']))
        return (0 < tick <= 1 / 30 and len(pts) == int(video[0]['nb_read_frames'])
                and all(math.isfinite(t) for t in pts)
                and all(b > a for a, b in zip(pts, pts[1:]))
                and all(abs(t - pts[0] - i / 30) <= tick + 0.000001 for i, t in enumerate(pts)))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False
