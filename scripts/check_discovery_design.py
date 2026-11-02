#!/usr/bin/env python3
"""Documentation-only SQL/JSON checks; stdlib, in-memory, no network or file writes."""
from pathlib import Path
import json
import re
import sqlite3

root = Path(__file__).resolve().parents[1]
c = sqlite3.connect(':memory:')
c.execute('PRAGMA foreign_keys=ON')
c.execute('PRAGMA recursive_triggers=ON')
for sql in re.findall(r'```sql\n(.*?)```', (root/'docs/data-and-state-contracts.md').read_text(), re.S):
    if 'CREATE ' in sql:
        c.executescript(sql)
s = (root/'docs/tool-discovery-agent.md').read_text()
blocks = re.findall(r'```sql\n(.*?)```', s, re.S)
c.execute("INSERT INTO tool_candidates(tool_id,tool_name,homepage,discovered_from,checked_on,decision) VALUES ('t','x','https://example.com','fixture','2026-11-02','selected')")
for sql in blocks:
    c.executescript(sql)
checks = 0

def check(condition):
    global checks
    assert condition
    checks += 1

def rejected(sql):
    global checks
    try:
        c.execute(sql)
    except sqlite3.IntegrityError:
        checks += 1
    else:
        raise AssertionError('Invalid fixture accepted')

check(c.execute("SELECT decision FROM tool_candidates WHERE tool_id='t'").fetchone()[0] == 'selected')
check('status' not in [x[1] for x in c.execute('PRAGMA table_info(tool_candidates)')])
for run in ['r1', 'r2']:
    c.execute("INSERT INTO discovery_runs(run_id,schema_version,mode,state,spec_json,spec_sha256) VALUES (?,1,'manual','running','{}','x')", (run,))
for state in ['needs_input', 'truncated', 'budget_blocked']:
    c.execute("INSERT INTO discovery_sources(run_id,source_id,adapter,state) VALUES ('r1',?,'html_listing',?)", (state, state))
    checks += 1
for i in [1, 2]:
    c.execute("INSERT INTO source_snapshots(snapshot_id,run_id,source_id,url,fetched_at,http_status,content_type,bytes,sha256,rel_path) VALUES (?,'r1','needs_input',?,'2026-11-02',200,'text/html',1,'same-hash','objects/same-hash')", ('s'+str(i), 'https://example.com/'+str(i)))
check(c.execute('SELECT count(*) FROM source_snapshots').fetchone()[0] == 2)
for i in [1, 2]:
    c.execute("INSERT INTO candidate_facts(fact_id,tool_id,field,fact_type,value_json,confidence,observed_at,expires_at,snapshot_id) VALUES (?,'t','price','vendor_claim',?,0.5,'2026-11-02','2026-12-02',?)", ('f'+str(i), str(i), 's'+str(i)))
check(c.execute('SELECT count(*) FROM candidate_facts').fetchone()[0] == 2)
for i in [1, 2]:
    c.execute("INSERT INTO discovery_items(run_id,item_key,tool_id,stage,state,payload_sha256,score,shortlist_rank,updated_at) VALUES (?,'item','t','scored','active',?, ?,?,'2026-11-02')", ('r'+str(i), 'payload'+str(i), 50+i, i))
check(c.execute('SELECT count(DISTINCT score) FROM discovery_items').fetchone()[0] == 2)
rejected("UPDATE discovery_items SET state='handed_off' WHERE run_id='r1'")
rejected("UPDATE discovery_items SET score=101 WHERE run_id='r1'")
check(c.execute('PRAGMA foreign_key_check').fetchall() == [])
objects = [json.loads(b) for b in re.findall(r'```json\n(.*?)```', s, re.S)]
spec, candidate = objects
check(spec['requested_targets'] in [3, 4, 5])
a = candidate['assessment']
check({f['id'] for f in a['hard_filters']} == {'HF'+str(i) for i in range(1, 8)})
check(candidate['status'] == 'needs_verification' and not candidate['test_handoff']['ready'] and a['score'] is None)
v = a['confidence_components']
check(round(.5*v['coverage']+.3*v['independence']+.2*v['freshness'], 4) == a['confidence'])
check(all(re.fullmatch('[0-9a-f]{64}', e['sha256']) for e in candidate['evidence']))
print(f'PASS {checks} documentation checks; {len(blocks)} discovery SQL blocks; {len(objects)} JSON examples')
print('Not product acceptance; migration version-runner and runtime enforcement are not implemented.')
