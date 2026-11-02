"""Untrusted source parsing, provenance store and conservative assessments."""
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

GATES = tuple('HF'+str(n) for n in range(1, 8))
REQUIRED = ('identity', 'browser', 'payment', 'access', 'safety', 'demo_path', 'recording_rights')
WEIGHTS = dict(zip(('visual', 'cost', 'chinese_fit', 'access', 'verifiability', 'freshness', 'difference'), (20,20,15,15,10,10,10)))

def now():
    return datetime.now(timezone.utc).isoformat()

def parse_time(value):
    if not isinstance(value, str): raise ValueError('ISO timestamp required')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        value += 'T00:00:00+00:00'
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('Timestamp timezone required')
    return parsed.astimezone(timezone.utc)

def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(value).hexdigest()

def canonical_url(value):
    p = urlsplit(value.strip())
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ValueError('Only credential-free HTTPS URLs are accepted')
    return urlunsplit(('https', p.netloc.lower(), p.path or '/', p.query, ''))

def parse_source(body, adapter):
    # UTF-8 only avoids encoding-dependent bypass of declaration checks.
    text = body.decode('utf-8-sig', errors='strict')
    if '\x00' in text or re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('DTD/entities/NUL forbidden')
    if adapter == 'rss':
        declaration = re.search(r'<\?xml[^>]*encoding\s*=\s*[\"\']([^\"\']+)', text, re.I)
        if declaration and declaration[1].lower() not in ('utf-8','utf8'):
            raise ValueError('Only UTF-8 XML supported')
        root = ET.fromstring(text)
        items = root.findall('./channel/item')
        if root.tag != 'rss':
            raise ValueError('Expected RSS 2.0')
        raw = [{'id': i.findtext('guid'), 'title': i.findtext('title'), 'url': i.findtext('link'),
                'published_at': i.findtext('pubDate'), 'summary': i.findtext('description') or ''} for i in items]
    elif adapter == 'json':
        data = json.loads(text)
        raw = data if isinstance(data, list) else data.get('items', data.get('data', []))
        if isinstance(raw, dict): raw = raw.get('items', [])
        if not isinstance(raw, list): raise ValueError('Expected items array')
    else:
        raise ValueError('Unknown adapter')
    output = []
    for item in raw[:500]:
        if not isinstance(item, dict): continue
        url = item.get('url') or item.get('link')
        title = item.get('title')
        if not isinstance(url,str) or not isinstance(title,str): continue
        try: url = canonical_url(url)
        except ValueError: continue
        summary = str(item.get('summary') or item.get('description') or '')[:12000]
        links = set()
        for link in re.findall(r'https://[^\s<>\"\']+', summary):
            try: links.add(canonical_url(link.replace('&amp;', '&')))
            except ValueError: pass
        links.add(url)
        output.append({'source_item_id': str(item.get('id') or url), 'title': title[:1000],
                       'url': url, 'published_at': item.get('published_at'), 'summary': summary,
                       'links': sorted(links), 'fact_type':'third_party_report', 'tool_id': None,
                       'status':'unresolved_research_lead', 'capability_tested':False})
    return output

class Store:
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'discovery.sqlite3')
        self.db.row_factory = sqlite3.Row
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1): raise ValueError('Unsupported database schema version')
        if version == 0:
            self.db.executescript('''BEGIN;
CREATE TABLE runs (id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL, result TEXT);
CREATE TABLE blobs (hash TEXT PRIMARY KEY, body BLOB NOT NULL);
CREATE TABLE retrievals (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
 url TEXT NOT NULL, final_url TEXT, fetched_at TEXT NOT NULL, status INTEGER, hash TEXT,
 stale INTEGER NOT NULL, error TEXT, etag TEXT, redirects TEXT);
CREATE TABLE occurrences (run_id TEXT NOT NULL, source_id TEXT NOT NULL, item_id TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(run_id,source_id,item_id));
CREATE TABLE entities (id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL);
PRAGMA user_version=1;
COMMIT;''')
        expected = {'runs','blobs','retrievals','occurrences','entities'}
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not expected <= tables: raise ValueError('Incomplete database schema')
    def start(self, run_id, spec):
        old = self.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        value = digest(spec)
        if old:
            if old['spec'] != value: raise ValueError('Run ID reused with different specification')
            return json.loads(old['result']) if old['state'] == 'complete' else None
        with self.db: self.db.execute('INSERT INTO runs VALUES(?,?,?,NULL)', (run_id,value,'running'))
    def cached(self, url):
        row = self.db.execute('''SELECT r.*,b.body FROM retrievals r JOIN blobs b ON r.hash=b.hash
WHERE url=? AND status=200 AND stale=0 ORDER BY rowid DESC LIMIT 1'''.replace('ORDER BY rowid','ORDER BY r.rowid'), (url,)).fetchone()
        return dict(row) if row else None
    def record(self, run_id, source_id, url, response=None, error=None, cached=None):
        response = response or {}
        status = response.get('status')
        use_cached = status == 304 or bool(error)
        if use_cached: body = cached['body'] if cached else None
        else: body = response.get('body')
        hash_ = digest(body) if body is not None else None
        rid = uuid.uuid4().hex
        fetched_at = now()
        final_url = response.get('final_url') or (cached['final_url'] if cached else url)
        stale = bool(error)
        etag = response.get('headers',{}).get('etag') or (cached['etag'] if cached else None)
        with self.db:
            if body is not None: self.db.execute('INSERT OR IGNORE INTO blobs VALUES(?,?)',(hash_,body))
            self.db.execute('INSERT INTO retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (rid,run_id,source_id,url,final_url,fetched_at,status,hash_,int(stale),error,etag,json.dumps(response.get('redirects',[]))))
        return {'retrieval_id':rid,'source_id':source_id,'url':url,'final_url':final_url,
                'fetched_at':fetched_at,'sha256':hash_,'stale':stale,'status':status,'error':error}, body
    def occurrence(self, run_id, source_id, item):
        key=(run_id,source_id,item['source_item_id'])
        value=json.dumps({k:v for k,v in item.items() if k not in ('retrieval_id','sha256','stale')},ensure_ascii=False,sort_keys=True)
        old=self.db.execute('SELECT payload FROM occurrences WHERE run_id=? AND source_id=? AND item_id=?',key).fetchone()
        if old and old[0] != value: raise ValueError('Conflicting occurrence within run')
        with self.db:self.db.execute('INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?)',(*key,value))
    def identity(self, identity):
        if not isinstance(identity,str) or not re.fullmatch(r'(operator|github_numeric|package):[^\s]{1,200}',identity):
            raise ValueError('Identity must be explicitly namespaced; domain alone insufficient')
        tid='tool_'+digest(identity)[:20]
        with self.db:self.db.execute('INSERT OR IGNORE INTO entities VALUES(?,?)',(tid,identity))
        return tid
    def finish(self, run_id, result):
        with self.db:self.db.execute('UPDATE runs SET state=?,result=? WHERE id=?',('complete',json.dumps(result,ensure_ascii=False),run_id))

def assess(annotation, evidence, timestamp):
    """Trusted researcher assertions only; website text never becomes authority."""
    def cite_valid(c):
        return isinstance(c,dict) and any(c.get('sha256')==e.get('sha256') and
            c.get('url') in (e.get('url'),e.get('final_url')) and not e.get('stale') and
            e.get('status') in (200,304) and isinstance(c.get('quote'),str) and bool(c['quote']) and c['quote'] in e.get('_text','') for e in evidence)
    evaluated_at = parse_time(timestamp)
    facts=annotation.get('facts',[])
    if not isinstance(facts,list): raise ValueError('facts must be array')
    for f in facts:
        if not isinstance(f,dict): raise ValueError('Each fact must be an object')
        if f.get('field') not in REQUIRED or f.get('fact_type') not in ('observed','vendor_claim','third_party_report','untested'):
            raise ValueError('Invalid fact field/type')
        if not isinstance(f.get('expires_at'),str): raise ValueError('Fact expiry required')
        parse_time(f['expires_at'])
    cited=[f for f in facts if cite_valid(f.get('citation')) and f.get('value') is not None]
    conflicting=[field for field in REQUIRED if len({json.dumps(f['value'],sort_keys=True) for f in cited if f['field']==field})>1]
    present={f['field'] for f in cited if f['field'] not in conflicting}
    fresh=[f for f in cited if parse_time(f['expires_at'])>evaluated_at]
    groups=sorted({f['control_group'] for f in cited if isinstance(f.get('control_group'),str) and f['control_group']})
    C=len(present)/len(REQUIRED); I=min(len(groups)/2,1); F=len(fresh)/len(cited) if cited else 0
    confidence=round(.5*C+.3*I+.2*F,4)
    gates={}
    for name in GATES:
        gate=annotation.get('gates',{}).get(name,{})
        status=gate.get('status','unknown')
        if status not in ('pass','fail','unknown'): raise ValueError('Invalid gate state')
        try: current = parse_time(gate.get('expires_at')) > evaluated_at
        except (ValueError, TypeError): current = False
        if status!='unknown' and (not cite_valid(gate.get('citation')) or not current): status='unknown'
        gates[name]={'status':status,'citation':gate.get('citation'),'reason':gate.get('reason','No verified evidence')}
    allpass=all(g['status']=='pass' for g in gates.values())
    primary=any(f.get('primary') is True and f in fresh for f in cited)
    eligible=allpass and not conflicting and primary and len(fresh)==len(cited)
    score=None;breakdown=None
    if eligible:
        breakdown={}
        for field,weight in WEIGHTS.items():
            raw=annotation.get('scores',{}).get(field,0)
            if isinstance(raw,bool) or not isinstance(raw,(int,float)) or not 0<=raw<=100:raise ValueError('Score outside 0..100')
            breakdown[field]={'raw':raw,'weight':weight,'contribution':raw*weight/100}
        score=round(sum(x['contribution'] for x in breakdown.values()),1)
    status='rejected' if any(g['status']=='fail' for g in gates.values()) else 'needs_verification'
    if score is not None and score>=60 and confidence>=.5:status='shortlisted'
    return {'status':status,'gates':gates,'facts':facts,'contradictions':conflicting,
      'score':score,'score_breakdown':breakdown,'confidence':confidence,'confidence_inputs':{
      'required':list(REQUIRED),'covered':len(present),'denominator':len(REQUIRED),'control_groups':groups,
      'fresh_count':len(fresh),'cited_count':len(cited),'evaluated_at':timestamp,'C':C,'I':I,'F':F},
      'capability_tested':False,'handoff_task_id':None}
