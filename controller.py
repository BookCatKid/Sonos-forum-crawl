"""Explicit phased public capture controller. Nothing runs at import or in background."""
import argparse,json,shutil,time,fcntl,functools
from pathlib import Path
from urllib.parse import urlsplit
from api_adapter import APIStore
from run_api import Session,Stop
from mirror import NOW

def exclusive(fn):
 @functools.wraps(fn)
 def wrapped(root,*args,**kwargs):
  Path(root).mkdir(parents=True,exist_ok=True)
  with (Path(root)/'controller.lock').open('a') as lock:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise ValueError('another controller holds this run; no concurrent batches')
   return fn(root,*args,**kwargs)
 return wrapped

@exclusive
def prepare(root,planfile):
 store=APIStore(root);store.db.execute('CREATE TABLE IF NOT EXISTS controller(key TEXT PRIMARY KEY,value TEXT)')
 with store.db:
  if store.db.execute("SELECT 1 FROM controller WHERE key='prepared'").fetchone():raise ValueError('already prepared; use status/resume, never duplicate enqueue')
  plan=json.loads(Path(planfile).read_text())
  for c in plan['categories']:
   if c['container']:continue
   reads=[c['first_request']]
   if c.get('needs_subpartition'):
    if not c.get('type_partitions'):raise ValueError('capped leaf lacks evidenced partitions')
    reads=[dict(c['first_request'],request=p) for p in c['type_partitions']]
   for r in reads+c.get('additional_reads',[]):store.task(r['url'],r['request'],'phase-listing',r.get('method','POST'))
  for r in plan.get('standalone_reads',[]):store.task(r['url'],r.get('request',{}),'standalone-listing',r.get('method','GET'))
  store.db.execute('INSERT INTO controller VALUES(?,?)',('prepared',NOW()));store.db.execute('INSERT INTO controller VALUES(?,?)',('phase','listing'))
 return store.report()

def is_reply(url):return urlsplit(url).path.startswith('/topic/') and urlsplit(url).path.endswith('/replies')
def phase_tasks(store,phase,state='pending'):
 return [r for r in store.db.execute("SELECT * FROM tasks WHERE state=? ORDER BY CASE WHEN kind='standalone-listing' THEN 0 ELSE 1 END,rowid",(state,)) if is_reply(r['url'])==(phase=='replies')]

@exclusive
def sync_standalone(root,planfile):
 store=APIStore(root);plan=json.loads(Path(planfile).read_text())
 for r in plan.get('standalone_reads',[]):store.task(r['url'],r.get('request',{}),'standalone-listing',r.get('method','GET'))
 store.db.commit();return status(root)

def usage(root):return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file())
def status(root):
 store=APIStore(root);r=store.report();r['disk_bytes']=usage(root);r['transport_stops']=[dict(x) for x in store.db.execute('SELECT * FROM transport_stops')] if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='transport_stops'").fetchone() else [];r['pending_by_kind']={r[0]:r[1] for r in store.db.execute("SELECT kind,count(*) FROM tasks WHERE state='pending' GROUP BY kind")};return r

@exclusive
def run_batch(root,requests,byte_budget=8_000_000_000,free_floor=2_000_000_000,delay=1,session_factory=Session,host_filter=None,store_factory=APIStore,stop_reason=str,max_seconds=None):
 if requests<1 or requests>500:raise ValueError('batch request budget must be1..500')
 if delay<1:raise ValueError('minimum delay1second')
 store=store_factory(root);store.db.execute('CREATE TABLE IF NOT EXISTS controller(key TEXT PRIMARY KEY,value TEXT)');store.db.execute('CREATE TABLE IF NOT EXISTS transport_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT)');store.db.commit()
 stopped_hosts={r[0] for r in store.db.execute('SELECT host FROM transport_stops')}
 if host_filter in stopped_hosts:raise Stop('persistent host stop requires review; no automatic reset')
 phase=store.db.execute("SELECT value FROM controller WHERE key='phase'").fetchone()
 if not phase or phase[0] not in ['listing','replies']:raise ValueError('prepare first / invalid phase')
 policy_file=Path(root)/'scope.json'
 if policy_file.exists():
  from scope_guard import load_scope,enforce
  enforce(store,load_scope(policy_file))
 sessions={};done=0;reason=None;started=time.monotonic();last_host=None;failure_filter_needed=True
 for i in range(requests):
  if max_seconds is not None and time.monotonic()-started>=max_seconds:reason='batch wall clock budget reached';break
  if usage(root)>=byte_budget or shutil.disk_usage(root).free<free_floor:reason='disk budget reached';break
  from fetch_failures import blocked,record
  failures=store.db.execute('SELECT 1 FROM fetch_failures LIMIT 1').fetchone() if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='fetch_failures'").fetchone() else None
  for candidate in (phase_tasks(store,phase[0]) if failures and failure_filter_needed else []):
   if blocked(store,candidate):
    with store.db:store.db.execute("UPDATE tasks SET state='failed' WHERE id=?",(candidate['id'],))
  failure_filter_needed=False
  rows=phase_tasks(store,phase[0]);rows=[r for r in rows if (not host_filter or urlsplit(r['url']).hostname==host_filter) and urlsplit(r['url']).hostname not in stopped_hosts];available=sorted({urlsplit(r['url']).hostname for r in rows});next_host=next((h for h in available if last_host is None or h>last_host),available[0] if available else None);row=next((r for r in rows if urlsplit(r['url']).hostname==next_host),None)
  if not row:reason='phase queue empty; review before explicit next phase';break
  host=urlsplit(row['url']).hostname;last_host=host
  reading_resource=False
  try:
   if host not in sessions:
    time.sleep(delay)
    sessions[host]=session_factory(host,root);sessions[host].bootstrap()
   session=sessions[host];policy=getattr(session,'policy',None);time.sleep(max(delay,policy.crawl_delay('*') or 0) if policy else delay)
   reading_resource=True
   e=session.read(row['url'],json.loads(row['request']));typ=None
   if '/topic/' in row['url']:
    public_id=urlsplit(row['url']).path.split('/')[2];t=store.db.execute('SELECT content_type FROM known_topic_types WHERE host=? AND topic_public_id=?',(host,public_id)).fetchone();typ=t[0] if t else None
   store.ingest(e,typ);done+=1
   with store.db:store.db.execute('INSERT OR REPLACE INTO controller VALUES(?,?)',('last_checkpoint',NOW()))
  except (Stop,OSError,ValueError) as e:
   code=stop_reason(e)
   if reading_resource and code not in {'HTTP 429','challenge','robots'}:
    record(store,row,code,NOW());failure_filter_needed=True;reason='resource fetch failed; logged gap, unrelated tasks remain';continue
   with store.db:store.db.execute('INSERT OR REPLACE INTO transport_stops VALUES(?,?,?)',(host,stop_reason(e),NOW()))
   reason=stop_reason(e);stopped_hosts.add(host);continue
 return {'captured_this_batch':done,'reason':reason or 'request budget reached','status':status(root),'phase':phase[0]}

@exclusive
def advance_replies(root,hosts=None,allow_coverage_gaps=False):
 store=APIStore(root)
 if phase_tasks(store,'listing'):raise ValueError('listing phase still pending')
 from audit_api_capture import audit
 coverage=audit(root)
 if coverage['issues'] or coverage['malformed_current_topics'] or (not allow_coverage_gaps and any(not f['count_consistent'] for f in coverage['listing_families'])):raise ValueError('listing receipt/count/ID audit is inconsistent; reconcile before replies')
 if store.db.execute("SELECT 1 FROM tasks WHERE state IN ('failed','stale') LIMIT 1").fetchone():raise ValueError('unreconciled failed/stale tasks')
 store.db.execute('CREATE TABLE IF NOT EXISTS transport_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT)')
 if store.db.execute('SELECT 1 FROM transport_stops LIMIT 1').fetchone():raise ValueError('persistent stop requires review')
 phase=store.db.execute("SELECT value FROM controller WHERE key='phase'").fetchone()
 if not phase or phase[0]!='listing':raise ValueError('only listing -> replies supported')
 topics=store.db.execute('SELECT host,public_id,raw_json FROM topic_rows').fetchall()
 if hosts:topics=[t for t in topics if t[0] in hosts]
 if allow_coverage_gaps:
  with store.db:store.db.execute('INSERT OR REPLACE INTO controller VALUES(?,?)',('listing_coverage_gaps',json.dumps(coverage)))
 if any(not json.loads(t[2]).get('contentType') for t in topics):raise ValueError('topic type missing; phase unchanged')
 policy_file=Path(root)/'scope.json'
 policy=None
 if policy_file.exists():
  from scope_guard import load_scope,permitted
  policy=load_scope(policy_file)
 for t in topics:
  if policy and not permitted('https://'+t[0]+'/topic/'+str(t[1])+'/replies',policy):continue
  store.plan_replies(t[0],t[1])
 with store.db:store.db.execute("UPDATE controller SET value='replies' WHERE key='phase'")
 return status(root)

@exclusive
def sync_replies(root,hosts,excluded_ids=()):
 store=APIStore(root)
 if store.db.execute("SELECT value FROM controller WHERE key='phase'").fetchone()[0]!='replies':raise ValueError('reply phase required')
 for row in store.db.execute('SELECT host,public_id FROM topic_rows').fetchall():
  if row[0] in hosts and row[1] not in excluded_ids:store.plan_replies(row[0],row[1])
 policy_file=Path(root)/'scope.json'
 if policy_file.exists():
  from scope_guard import load_scope,enforce
  enforce(store,load_scope(policy_file))
 store.db.commit();return status(root)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','status','run-batch','advance-replies','sync-standalone','sync-replies']);p.add_argument('--root',type=Path,required=True);p.add_argument('--plan',type=Path);p.add_argument('--request-budget',type=int,default=100);p.add_argument('--hosts',nargs='+');p.add_argument('--allow-coverage-gaps',action='store_true');p.add_argument('--host');p.add_argument('--exclude-ids',nargs='*',default=[]);a=p.parse_args()
 if a.action=='prepare':r=prepare(a.root,a.plan)
 elif a.action=='status':r=status(a.root)
 elif a.action=='advance-replies':r=advance_replies(a.root,a.hosts,a.allow_coverage_gaps)
 elif a.action=='sync-replies':r=sync_replies(a.root,a.hosts or [],a.exclude_ids)
 elif a.action=='sync-standalone':r=sync_standalone(a.root,a.plan)
 else:r=run_batch(a.root,a.request_budget,host_filter=a.host)
 print(json.dumps(r,indent=2))
