import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from discovery_agent.core import parse_time, Store, parse_source, assess, REQUIRED, GATES, digest
from discovery_agent.__main__ import run, load_config

BODY=Path('fixtures/discovery/products.xml').read_bytes()
URL='https://news.example/feed.xml'
CONFIG={'purpose':'internal_research','allowed_domains':['news.example'],'sources':[{'id':'news','url':URL,'adapter':'rss'}]}
class Fake:
    def __init__(self, responses):self.responses=list(responses);self.calls=0
    def fetch(self,url,etag=None):
        self.calls+=1
        response=self.responses.pop(0)
        if isinstance(response,Exception):raise response
        return response

def response(body=BODY,status=200):return {'body':body,'status':status,'headers':{'etag':'example','content-type':'application/rss+xml'},'final_url':URL,'redirects':[]}

class CoreTests(unittest.TestCase):
    def test_timestamp_validation(self):
        self.assertEqual(parse_time('2026-11-02T08:00:00+08:00'), parse_time('2026-11-02T00:00:00Z'))
        self.assertEqual(parse_time('2026-11-02'), parse_time('2026-11-02T00:00:00Z'))
        for value in ('garbage', '2026-11-02T00:00:00'):
            with self.assertRaises(ValueError):parse_time(value)
        with self.assertRaises(ValueError):assess({'facts':['invalid']},[],'2026-11-02')
    def test_markdown_html_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            body=BODY.replace(b'Example tool launch',b'&lt;script&gt;alert(1)&lt;/script&gt;')
            run(CONFIG,d,'html',fetcher=Fake([response(body)]))
            summary=(Path(d)/'runs/html/summary.md').read_text()
            self.assertNotIn('<script>',summary);self.assertIn('&lt;script&gt;',summary)
    def test_rss_occurrences_not_entities(self):
        a=parse_source(BODY,'rss');self.assertEqual(len(a),2);self.assertIsNone(a[0]['tool_id'])
        self.assertIn('https://tool.example/',a[0]['links'])
    def test_json(self):
        a=parse_source(b'{"items":[{"id":"x","title":"X","url":"https://tool.example/"}]}','json')
        self.assertEqual(a[0]['source_item_id'],'x')
    def test_bad_xml(self):
        for body in (b'<!DOCTYPE rss><rss/>',b'<!ENTITY x "x"><rss/>','<rss/>'.encode('utf-16'),b'<?xml version="1.0" encoding="UTF-16"?><rss/>'):
            with self.assertRaises((ValueError,UnicodeError)):parse_source(body,'rss')
    def test_provenance_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d)
            for n in range(2):s.record(str(n),'source',URL,response())
            self.assertEqual(s.db.execute('SELECT count(*) FROM blobs').fetchone()[0],1)
            self.assertEqual(s.db.execute('SELECT count(*) FROM retrievals').fetchone()[0],2)
            self.assertEqual(s.identity('operator:example-tool'),s.identity('operator:example-tool'))
            with self.assertRaises(ValueError):s.identity('tool.example')
            s.db.close()
    def test_completed_idempotent_and_config_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            fetch=Fake([response()]);a=run(CONFIG,d,'one',fetcher=fetch)
            b=run(CONFIG,d,'one',fetcher=fetch)
            self.assertEqual(a,b);self.assertEqual(fetch.calls,1)
            self.assertEqual(len(a['leads']),2);self.assertFalse(a['shortlist'])
            self.assertIn('tool.example',a['proposed_source_domains'])
            with self.assertRaises(ValueError):run({**CONFIG,'extra':'changed'},d,'one',fetcher=fetch)
    def test_cached_error_stale_and_304(self):
        with tempfile.TemporaryDirectory() as d:
            run(CONFIG,d,'first',fetcher=Fake([response()]))
            stale=run(CONFIG,d,'error',fetcher=Fake([OSError('transient')]))
            self.assertEqual(stale['state'],'partial');self.assertTrue(stale['evidence'][0]['stale']);self.assertEqual(len(stale['leads']),2)
            fresh=run(CONFIG,d,'checked',fetcher=Fake([response(b'',304)]))
            self.assertFalse(fresh['evidence'][0]['stale']);self.assertEqual(len(fresh['leads']),2)
            db=sqlite3.connect(Path(d)/'discovery.sqlite3')
            self.assertEqual(db.execute('SELECT count(*) FROM retrievals').fetchone()[0],3);db.close()
    def test_wrong_mime_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            wrong=response();wrong['headers']['content-type']='text/html'
            a=run(CONFIG,d,'mime',fetcher=Fake([wrong]))
            self.assertFalse(a['leads']);self.assertEqual(a['state'],'partial')

    def test_partial_source_failure(self):
        with tempfile.TemporaryDirectory() as d:
            config={**CONFIG,'sources':CONFIG['sources']+[{'id':'second','adapter':'rss','url':URL+'?x=2'}]}
            a=run(config,d,'partial',fetcher=Fake([OSError('timeout'),response()]))
            self.assertEqual(len(a['leads']),2);self.assertEqual(a['state'],'partial')
    def test_invalid_config_migration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text('{}')
            with self.assertRaises(ValueError):load_config(p)
            s=Store(d);s.db.execute('PRAGMA user_version=999');s.db.close()
            with self.assertRaises(ValueError):Store(d)
    def test_unknown_gates_and_confidence(self):
        ev=[{'url':URL,'sha256':'abc','status':200,'stale':False,'_text':'claim'}]
        facts=[{'field':f,'value':True,'fact_type':'vendor_claim','expires_at':'2027-01-01T00:00:00+00:00','control_group':'vendor',
                'citation':{'url':URL,'sha256':'abc','quote':'claim'}} for f in REQUIRED[:3]]
        a=assess({'facts':facts},ev,'2026-11-02T00:00:00+00:00')
        self.assertEqual(a['confidence'],round(.5*3/7+.3*.5+.2,4));self.assertIsNone(a['score']);self.assertEqual(a['status'],'needs_verification')
        conflict={**facts[0],'value':False}
        b=assess({'facts':facts+[conflict]},ev,'2026-11-02T00:00:00+00:00')
        self.assertIn('identity',b['contradictions']);self.assertEqual(b['confidence_inputs']['covered'],2)
    def test_quote_validation_and_unknown_gate(self):
        ev=[{'url':URL,'sha256':'abc','status':200,'stale':False,'_text':'actual'}]
        citation={'url':URL,'sha256':'abc','quote':'invented'}
        a=assess({'gates':{g:{'status':'pass','citation':citation,'expires_at':'2027'} for g in GATES}},ev,'2026-11-02T00:00:00+00:00')
        self.assertTrue(all(g['status']=='unknown' for g in a['gates'].values()))
    def test_entity_annotation_does_not_auto_qualify(self):
        with tempfile.TemporaryDirectory() as d:
            annotation={'name':'Example','identity':'operator:example','occurrences':[{'source_id':'news','source_item_id':'news-1'},{'source_id':'news','source_item_id':'news-2'}]}
            a=run(CONFIG,d,'annotated',[annotation],Fake([response()]))
            self.assertEqual(len(a['candidates']),1);self.assertFalse(a['shortlist'])

if __name__=='__main__':unittest.main()
