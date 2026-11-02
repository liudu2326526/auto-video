import argparse
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from .core import Store, assess, canonical_url, digest, now, parse_source


def load_config(path):
    config=json.loads(Path(path).read_text())
    if config.get('purpose')!='internal_research':raise ValueError('Only internal_research is supported')
    if not isinstance(config.get('sources'),list) or not 1<=len(config['sources'])<=10:raise ValueError('1..10 sources required')
    ids=set()
    for s in config['sources']:
        if not isinstance(s.get('id'),str) or s['id'] in ids:raise ValueError('Unique source IDs required')
        ids.add(s['id']);canonical_url(s['url'])
        if s.get('adapter') not in ('rss','json','evidence'):raise ValueError('Unknown source adapter')
    if not isinstance(config.get('allowed_domains'),list) or not config['allowed_domains']:raise ValueError('Explicit allowed_domains required')
    return config


def atomic_json(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def write_outputs(root,run_id,result):
    folder=Path(root)/'runs'/run_id;folder.mkdir(parents=True,exist_ok=True)
    for name,value in [('run',result),('candidates',result['candidates']),('shortlist',result['shortlist']),('leads',result['leads'])]:
        atomic_json(folder/(name+'.json'),value)
    lines=['# Tool discovery — internal research only','',f"Run: `{run_id}`",'',
           f"Research leads: {len(result['leads'])}; named tools: {len(result['candidates'])}; qualified shortlist: {len(result['shortlist'])}",
           '',result['shortfall_reason'],'','## Leads (not tool identity or tested capabilities)']
    for lead in result['leads']:
        # JSON-escape source strings to avoid interpreting untrusted Markdown/HTML.
        lines.append('- '+re.sub(r'([\[\]()*_`!#])', r'\\\1', html.escape(json.dumps(lead['title'],ensure_ascii=False)))+' — '+html.escape(lead['url']))
    lines+=['','## Proposed domains (not automatically approved)',', '.join(result['proposed_source_domains']),
            '', 'No accounts, paid APIs, recording, publication or avcd handoff were performed.']
    temp=folder/'summary.tmp';temp.write_text('\n'.join(lines)+'\n');temp.replace(folder/'summary.md')
    return folder


def run(config,root,run_id,annotations=None,fetcher=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id):raise ValueError('Invalid run ID')
    if fetcher is None:
        from .net import Fetcher
        fetcher=Fetcher(config)
    annotations=annotations or []
    if not isinstance(annotations,list):raise ValueError('Annotations must be an array')
    store=Store(root)
    spec={'config':config,'annotations':annotations,'version':1}
    old=store.start(run_id,spec)
    if old:
        write_outputs(root,run_id,old);store.db.close();return old
    evidence=[];leads=[];errors=[];domains=set();timestamp=now()
    for source in config['sources']:
        url=source['url'];cached=store.cached(url);response=None;error=None
        try:
            response=fetcher.fetch(url,etag=cached['etag'] if cached else None)
            if response['status'] not in (200,304):raise ValueError('HTTP '+str(response['status']))
            if response['status']==304 and not cached:raise ValueError('304 without cached body')
            if response['status']==200:
                mime=response.get('headers',{}).get('content-type','').split(';')[0].strip().lower()
                allowed={'rss': {'application/rss+xml','application/xml','text/xml'},
                         'json': {'application/json'},
                         'evidence': {'text/html','text/plain','application/xhtml+xml','application/json'}}
                if mime not in allowed[source['adapter']]:raise ValueError('Unexpected content type: '+mime)
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)[:300];errors.append({'source_id':source['id'],'error':error})
        ev,body=store.record(run_id,source['id'],url,response,error,cached)
        # Text is internal validation context only, never included in final result.
        ev['_text']=body.decode('utf-8',errors='replace') if body else ''
        evidence.append(ev)
        if body and source['adapter']!='evidence':
            try:
                for item in parse_source(body,source['adapter']):
                    item['source_id']=source['id'];item['retrieval_id']=ev['retrieval_id'];item['sha256']=ev['sha256'];item['stale']=ev['stale']
                    store.occurrence(run_id,source['id'],item)
                    key=(source['id'],item['source_item_id'])
                    if not any((l['source_id'],l['source_item_id'])==key for l in leads):leads.append(item)
                    for link in item['links']:
                        domain=urlsplit(link).hostname
                        if domain not in config['allowed_domains']:domains.add(domain)
            except Exception as exc: errors.append({'source_id':source['id'],'error':'parse: '+str(exc)[:300]})
    candidates=[]
    for annotation in annotations:
        if not isinstance(annotation,dict) or not isinstance(annotation.get('name'),str):raise ValueError('Named annotation required')
        tid=store.identity(annotation.get('identity'))
        if any(c['tool_id']==tid for c in candidates):raise ValueError('Duplicate entity annotation: combine facts without discarding conflicts')
        selected=annotation.get('occurrences',[])
        if not selected or not all(any(l['source_id']==o.get('source_id') and l['source_item_id']==o.get('source_item_id') for l in leads) for o in selected):
            raise ValueError('Annotation must cite retrieved occurrence(s)')
        candidate={'tool_id':tid,'name':annotation['name'],'identity':annotation['identity'],'occurrences':selected,
                   'assessment':assess(annotation,evidence,timestamp)}
        candidates.append(candidate)
    public_evidence=[{k:v for k,v in e.items() if k!='_text'} for e in evidence]
    shortlist=[c for c in candidates if c['assessment']['status']=='shortlisted']
    result={'schema_version':1,'run_id':run_id,'state':'partial' if errors else 'complete','checked_at':timestamp,
            'purpose':'internal_research','leads':leads,'candidates':candidates,'shortlist':shortlist,
            'evidence':public_evidence,'errors':errors,'proposed_source_domains':sorted(domains),
            'shortfall_reason': 'No qualified tool: identity, primary-source facts and all seven gates require cited trusted review.' if not shortlist else 'Shortlist is not tested or handed off.',
            'integration':'standalone; avcd integration not implemented'}
    # Result durable first; output writing can be replayed without fetching again.
    store.finish(run_id,result);write_outputs(root,run_id,result);store.db.close()
    return result


def main():
    parser=argparse.ArgumentParser(description='Manual internal tool discovery; no LLM, recording or publication.')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('run',help='Collect approved sources and emit unresolved research leads/cited assessments')
    p.add_argument('--config',required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--root',default='var/discovery');p.add_argument('--annotations',help='Trusted operator JSON, never source-provided')
    args=parser.parse_args()
    try:
        config=load_config(args.config)
        annotations=json.loads(Path(args.annotations).read_text()) if args.annotations else None
        result=run(config,args.root,args.run_id,annotations)
        print(json.dumps({'run_id':result['run_id'],'state':result['state'],'leads':len(result['leads']),
                          'tools':len(result['candidates']),'qualified':len(result['shortlist']),
                          'output':str(Path(args.root)/'runs'/args.run_id)},ensure_ascii=False))
        return 0 if not result['errors'] else 2
    except (ValueError,OSError) as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
