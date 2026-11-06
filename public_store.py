"""New public-safe captures only. No raw-session checkpoint conversion."""
import gzip,json,sqlite3
from api_adapter import APIStore,validate
from public_payload import project,public_request,PublicationError
from scope_guard import load_scope,permitted

class PublicStore(APIStore):
 def ingest(self,e,topic_type=None):
  e=project(e)
  from urllib.parse import urlsplit
  if urlsplit(e['url']).hostname=='en.community.sonos.com':
   policy=load_scope(self.root/'scope.json')
   topic_list=e['response'].get('topics',e['response'].get('result',{}).get('topics',[]))
   if isinstance(topic_list,dict):topic_list=[t for values in topic_list.values() for t in values]
   if any(str(t.get('publicId')) in policy['excluded_en_topic_ids'] for t in topic_list):raise PublicationError('listing contains carried excluded identity; review boundary before storage')
  # Public capture is an observed-ID union, not a live visibility snapshot.
  # Page drift must not erase previously captured topics from future reply plans.
  import hashlib
  tid=hashlib.sha256((e['url']+json.dumps(e.get('request') or {},sort_keys=True)).encode()).hexdigest()
  prior=list(self.db.execute('SELECT task_id,host,public_id,raw_json FROM topic_observations WHERE task_id=?',(tid,)))
  result=super().ingest(e,topic_type)
  if prior:
   with self.db:
    self.db.executemany('INSERT OR IGNORE INTO topic_observations VALUES(?,?,?,?)',[tuple(r) for r in prior])
    host=urlsplit(e['url']).hostname
    self.db.execute('DELETE FROM topic_rows WHERE host=?',(host,))
    self.db.execute('INSERT OR REPLACE INTO topic_rows SELECT host,public_id,raw_json FROM topic_observations WHERE host=? ORDER BY rowid',(host,))
  return result

def check_public(root):
 """Publication preflight: schema checks, not textual content analysis."""
 from pathlib import Path
 root=Path(root);policy=load_scope(root/'scope.json')
 db=sqlite3.connect(root/'api.sqlite');db.row_factory=sqlite3.Row
 try:
  from public_state import validate_state
  validate_state(db,root)
  expected={'topic_observations','receipts','tasks','topic_rows','reply_rows','reply_expectations','known_topic_types','reply_observations','refreshed_tasks','totals','controller','transport_stops','resource_stops','policy_exclusions','event_pages'}
  tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if tables-expected:raise PublicationError('unreviewed database tables')
  receipts={};topics={};replies={};events={};paths=set()
  for r in db.execute('SELECT * FROM receipts ORDER BY rowid'):
   rel=Path(r['path'])
   if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='json':raise PublicationError('receipt path outside json tree')
   paths.add(str(rel));e=json.loads(gzip.decompress((root/rel).read_bytes()))
   if project(e)!=e:raise PublicationError('raw or nonminimal receipt requires explicit migration')
   if not permitted(e['url'],policy):raise PublicationError('excluded receipt')
   if r['url']!=e['url'] or r['method']!=e['method'] or r['request']!=json.dumps(e['request'],sort_keys=True) or r['status']!=e['status']:raise PublicationError('receipt metadata mismatch')
   tid=__import__('hashlib').sha256((e['url']+json.dumps(e['request'],sort_keys=True)).encode()).hexdigest();host=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(e['url']).hostname
   b=e['response']
   if '/events/events' in e['url']:events[e['url']]=b
   topic_list=b.get('topics',b.get('result',{}).get('topics',[]))
   if isinstance(topic_list,dict):topic_list=[t for values in topic_list.values() for t in values]
   if host=='en.community.sonos.com' and any(str(t.get('publicId')) in policy['excluded_en_topic_ids'] for t in topic_list):raise PublicationError('excluded topic in public listing receipt')
   for t in topic_list:topics[(tid,host,str(t['publicId']))]=t
   def children(c,topic,parent):
    for x in c['result']:
     replies[(tid,host,topic,str(x['id']))]=x
     if x.get('children'):children(x['children'],topic,str(x['id']))
   if 'replies' in b:children(b['replies'],e['url'].split('/topic/')[1].split('/')[0],'')
  if {str(p.relative_to(root)) for p in (root/'json').rglob('*') if p.is_file()}!=paths:raise PublicationError('untracked receipt files')
  if (root/'discovery-plan.json').exists():
   from public_plan import validate_pinned
   validate_pinned(json.loads((root/'discovery-plan.json').read_text()))
  # Indexed backing sets avoid rescanning every receipt for each projection.
  from collections import defaultdict
  topic_backing=defaultdict(set);reply_backing=defaultdict(set)
  canonical=lambda v:json.dumps(v,sort_keys=True,ensure_ascii=False)
  for (tid,h,i),v in topics.items():
   topic_backing[(h,i)].add(canonical(v));topic_backing[(tid,h,i)].add(canonical(v))
  for (tid,h,t,i),v in replies.items():
   reply_backing[(h,t,i)].add(canonical(v));reply_backing[(tid,h,t,i)].add(canonical(v))
  for table in ('topic_rows','topic_observations'):
   for row in db.execute('SELECT * FROM '+table):
    key=(row['host'],row['public_id'])
    if 'task_id' in row.keys():key=(row['task_id'],)+key
    if canonical(json.loads(row['raw_json'])) not in topic_backing[key]:raise PublicationError('unbacked public topic projection')
  for table in ('reply_rows','reply_observations'):
   for row in db.execute('SELECT * FROM '+table):
    key=(row['host'],row['topic_public_id'],row['internal_id'])
    if 'task_id' in row.keys():key=(row['task_id'],)+key
    if canonical(json.loads(row['raw_json'])) not in reply_backing[key]:raise PublicationError('unbacked public reply projection')
  if 'event_pages' in tables:
   for row in db.execute('SELECT * FROM event_pages'):
    if events.get(row['url'])!=json.loads(row['raw_json']):raise PublicationError('unbacked event projection')
  for row in db.execute('SELECT * FROM tasks'):
   url=row['url'];public_request(url,'GET' if '/events/events' in url or '/productUpdates/topics' in url else 'POST',json.loads(row['request']))
  return {'public_schema_version':1,'receipts_checked':db.execute('SELECT count(*) FROM receipts').fetchone()[0]}
 finally:db.close()
