#!/usr/bin/env python3
"""Validate design-document SQL/JSON examples using only the Python standard library.

This is a documentation consistency check, not product implementation or end-to-end
acceptance. It uses a fresh in-memory SQLite database and synthetic fixtures. It
makes no network requests and does not modify repository files or runtime data.
Run from any directory: python3 scripts/check_design_contracts.py
"""
from pathlib import Path
import re,json,sqlite3
root = Path(__file__).resolve().parents[1]
s=(root/'docs/data-and-state-contracts.md').read_text()
c=sqlite3.connect(':memory:'); c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA recursive_triggers=ON')
n=0
for b in re.findall(r'```sql\n(.*?)```',s,re.S):
 if 'CREATE ' in b:
  c.executescript(b); n+=1
print('DDL blocks:',n,'tables:',c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
passed=[]
def ok(name,fn):
 fn(); passed.append(name)
def bad(name,sql,args=()):
 c.execute('SAVEPOINT negative')
 try: c.execute(sql,args)
 except sqlite3.IntegrityError:
  passed.append(name)
 else: raise AssertionError(name+' accepted invalid input')
 finally: c.execute('ROLLBACK TO negative'); c.execute('RELEASE negative')
def ins(t,**kw):
 c.execute('INSERT INTO '+t+'('+','.join(kw)+') VALUES ('+','.join('?' for _ in kw)+')',list(kw.values()))
for j in ['j1','j2']:
 ins('jobs',job_id=j,title='fixture',status='planned',current_revision=1,target_platforms='[]',template_id='t',created_at='2026-01-01',updated_at='2026-01-01')
ins('tool_candidates',tool_id='tool',tool_name='x',homepage='https://example.com',discovered_from='official',checked_on='2026-01-01',decision='selected')
for a in ['input','screen','report','mp4','pkg']:
 ins('assets',asset_id=a,sha256=a,kind='report',bytes=1,rel_path='objects/'+a,origin='first_party',created_at='2026-01-01')
tr=dict(job_id='j1',tool_id='tool',tool_version='1',environment='{}',input_sha256='input',started_at='2026-01-01T00:00:00Z',ended_at='2026-01-01T00:01:00Z',result='success',outcome_note='ok',succeeded=1)
ins('test_runs',test_run_id='tr1',**tr); ins('test_runs',test_run_id='tr2',**tr); passed.append('same-day retest allowed')
bad('contradictory success rejected',"UPDATE test_runs SET result='failure',succeeded=1 WHERE test_run_id='tr1'")
ins('script_versions',script_version_id='sv',job_id='j1',version=1,state='draft',inputs_digest='d')
ins('script_test_basis',script_version_id='sv',test_run_id='tr1',role='primary')
ins('script_versions',script_version_id='sv2',job_id='j2',version=1,state='draft',inputs_digest='d')
bad('cross-job basis rejected',"INSERT INTO script_test_basis VALUES ('sv2','tr1','primary')")
bad('basis required',"UPDATE script_versions SET state='frozen',frozen_at='2026-01-01T00:02:00Z' WHERE script_version_id='sv2'")
ins('script_lines',line_id='line',script_version_id='sv',seq=1,text='claim')
ins('claims',claim_id='cl',job_id='j1',text='claim',kind='capability',status='supported')
ins('line_claims',line_id='line',claim_id='cl')
bad('status alone no proof',"UPDATE script_versions SET state='frozen',frozen_at='2026-01-01T00:02:00Z' WHERE script_version_id='sv'")
ins('evidence',evidence_id='ev',test_run_id='tr1',asset_id='screen',kind='screen',captured_at='2026-01-01T00:01:00Z',description='result')
ins('claim_evidence',claim_id='cl',evidence_id='ev',relation='supports')
bad('freeze before ended rejected',"UPDATE script_versions SET state='frozen',frozen_at='2026-01-01T00:00:30Z' WHERE script_version_id='sv'")
c.execute("UPDATE script_versions SET state='frozen',frozen_at='2026-01-01T00:02:00Z' WHERE script_version_id='sv'"); passed.append('valid freeze accepted')
ins('edit_plans',edit_plan_id='ep',job_id='j1',version=1,script_version_id='sv',template_id='t',template_sha256='t',renderer='ffmpeg',renderer_version='1',plan_json='{}',plan_sha256='p',state='rendered')
ins('video_versions',video_version_id='vv',job_id='j1',version=1,edit_plan_id='ep',mp4_asset_id='mp4',state='qa_passed')
ins('tasks',task_id='task',job_id='j1',stage='qa',kind='qa',revision=1,state='running',created_at='2026-01-01',updated_at='2026-01-01')
ins('attempts',attempt_id='attempt',task_id='task',attempt_no=1,state='running',executor='reviewer',session_id='s',capability='qa',heartbeat_at='2026-01-01',deadline_at='2026-01-02',created_at='2026-01-01')
bad('one running attempt',"INSERT INTO attempts SELECT 'attempt2',task_id,2,state,executor,session_id,pid,capability,heartbeat_at,deadline_at,outputs_digest,checkpoint,created_at,ended_at FROM attempts WHERE attempt_id='attempt'")
qa=dict(qa_run_id='qa',video_version_id='vv',attempt_id='attempt',mp4_sha256='mp4',verdict='pass',checks='{}',full_decode_ok=1,coverage_ratio=1,report_asset_id='report',created_at='2026-01-01')
ins('qa_runs',**qa)
bad('98percent not full QA',"UPDATE qa_runs SET coverage_ratio=0.98 WHERE qa_run_id='qa'")
ins('qa_approvals',approval_id='qa-a',qa_run_id='qa',video_version_id='vv',mp4_sha256='mp4',plan_revision=1,state='valid',issued_at='2026-01-01')
ins('publish_intents',intent_id='intent',video_version_id='vv',platform='douyin',account_ref='a',package_asset_id='pkg',package_sha256='pkg',plan_revision=1,dedupe_key='key',mode='human_handoff',state='approved')
bad('approved package account immutable',"UPDATE publish_intents SET account_ref='other' WHERE intent_id='intent'")
ins('external_operations',op_id='op',intent_id='intent',provider='human',operation='create',idempotency_key='k',request_digest='digest',state='PREPARED')
bad('claim required',"UPDATE external_operations SET state='SUBMITTING' WHERE op_id='op'")
c.execute("UPDATE external_operations SET state='SUBMITTING',send_claim_id='claim' WHERE op_id='op'"); passed.append('first send claim accepted')
bad('claim replay rejected',"UPDATE external_operations SET state='SUBMITTING' WHERE op_id='op'")
bad('request mutation rejected',"UPDATE external_operations SET request_digest='new' WHERE op_id='op'")
c.execute("UPDATE external_operations SET state='UNKNOWN' WHERE op_id='op'")
bad('unknown replay rejected',"UPDATE external_operations SET state='PREPARED' WHERE op_id='op'")
ins('budget_accounts',job_id='j1',limit_micros=100,mode='hard')
base=dict(job_id='j1',bound_kind='enforced_max',bound_basis='fixture cap',category='llm',state='held',created_at='2026-01-01')
ins('budget_reservations',reservation_id='r1',operation_key='o1',bound_micros=60,**base)
ins('budget_reservations',reservation_id='r2',operation_key='o2',bound_micros=30,**base)
bad('over-budget rejected',"INSERT INTO budget_reservations(reservation_id,job_id,operation_key,bound_micros,bound_kind,bound_basis,category,state,created_at) VALUES ('r3','j1','o3',20,'enforced_max','x','llm','held','x')")
bad('hard estimate rejected',"INSERT INTO budget_reservations(reservation_id,job_id,operation_key,bound_micros,bound_kind,bound_basis,category,state,created_at) VALUES ('r3','j1','o3',1,'estimate','x','llm','held','x')")
c.execute("UPDATE budget_reservations SET state='unknown' WHERE reservation_id='r1'")
assert c.execute("SELECT reserved_micros FROM budget_totals WHERE job_id='j1'").fetchone()[0]==90; passed.append('unknown retains reserve')
c.execute("UPDATE budget_reservations SET state='released' WHERE reservation_id='r2'")
bad('repeat release with other reserve rejected',"UPDATE budget_reservations SET state='released' WHERE reservation_id='r2'")
c.execute("UPDATE budget_reservations SET state='settled',actual_micros=50,settlement_key='settle1' WHERE reservation_id='r1'")
bad('repeat settlement rejected',"UPDATE budget_reservations SET actual_micros=50 WHERE reservation_id='r1'")
assert c.execute("SELECT reserved_micros,spent_micros FROM budget_totals WHERE job_id='j1'").fetchone()==(0,50); passed.append('budget aggregation correct')
c.execute("UPDATE evidence SET quarantined=1 WHERE evidence_id='ev'")
assert c.execute("SELECT state FROM script_versions WHERE script_version_id='sv'").fetchone()[0]=='invalidated'
assert c.execute("SELECT state FROM qa_approvals WHERE approval_id='qa-a'").fetchone()[0]=='invalidated'
assert c.execute("SELECT state FROM publish_intents WHERE intent_id='intent'").fetchone()[0]=='cancelled'; passed.append('evidence cascade invalidates approval intent')
assert not c.execute('PRAGMA foreign_key_check').fetchall(); passed.append('foreign-key check clean')
j=0
for path in [root/'docs/data-and-state-contracts.md',root/'docs/validation-plan.md']:
 for block in re.findall(r'```json\n(.*?)```',path.read_text(),re.S): json.loads(block); j+=1
print('PASS',len(passed),'contract fixtures; JSON blocks',j)
for v in passed: print('-',v)
