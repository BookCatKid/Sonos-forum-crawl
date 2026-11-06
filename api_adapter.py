"""Evidence-backed public read API task planner and content-addressed JSON capture."""
import argparse,gzip,hashlib,json,re,sqlite3
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl,urlencode,urlunsplit
from mirror import HOSTS,NOW

def unwrap(x):return json.loads(x['content']) if isinstance(x,dict) and 'content' in x else x

def validate(url,method,request):
 p=urlsplit(url)
 if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password or p.port not in [None,443]:raise ValueError('unsafe origin')
 from group_discovery import group_path
 if method=='GET' and group_path(p.path) and not p.query and not p.fragment:return p
 if method=='GET' and p.path in ['/productUpdates/topics','/events/events']:
  if any(k not in ['page','tab','type'] for k,v in parse_qsl(p.query)):raise ValueError('unsupported GET query')
  return p
 if method!='POST':raise ValueError('only validated read contracts supported')
 if p.path=='/contentNavigation/topics':
  if not isinstance(request.get('sort'),dict) or not isinstance(request.get('pagination'),dict):raise ValueError('invalid listing request')
  pag=request['pagination']
  if not isinstance(pag.get('page'),int) or pag['page']<1 or not 1<=pag.get('pageSize',0)<=25:raise ValueError('invalid listing pagination')
 elif re.fullmatch(r'/topic/\d+/replies',p.path):
  if request.get('offset',-1)<0 or not 1<=request.get('pageSize',0)<=20 or request.get('sort')!='oldestFirst':raise ValueError('invalid replies request')
  parent=request.get('parentId')
  if parent and (parent.get('contentType') not in ['question','conversation','article','idea','productUpdate'] or not isinstance(parent.get('id'),int)):raise ValueError('invalid parent')
 elif p.path in ['/categoryV2/listReadableCategoryContent','/categoryV2/listReadableCategoryOrderTopics','/categoryV2/getReadableCuratedContent']:
  if not isinstance(request.get('categoryId'),int) or request['categoryId']<=0:raise ValueError('invalid evidenced category ID')
  if p.path!='/categoryV2/listReadableCategoryOrderTopics':
   pag=request.get('pagination',{})
   if request.get('type') is not None or not isinstance(pag.get('page'),int) or pag['page']<1 or not 1<=pag.get('pageSize',0)<=25:raise ValueError('only observed null-type curation contract supported')
 elif p.path=='/categoryV2/getAccessibleCategoriesTree':
  if request.get('type')!=[0]:raise ValueError('only observed public community tree supported')
 else:raise ValueError('not a validated public read route')
 return p

class APIStore:
 def __init__(self,root):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(self.root/'api.sqlite');self.db.row_factory=sqlite3.Row
  self.db.executescript('''CREATE TABLE IF NOT EXISTS topic_observations(task_id TEXT,host TEXT,public_id TEXT,raw_json TEXT,PRIMARY KEY(task_id,public_id));CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,url TEXT,method TEXT,request TEXT,status INTEGER,path TEXT,observed_at TEXT);CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,url TEXT,request TEXT,kind TEXT,state TEXT DEFAULT 'pending');CREATE TABLE IF NOT EXISTS topic_rows(host TEXT,public_id TEXT,raw_json TEXT,PRIMARY KEY(host,public_id));CREATE TABLE IF NOT EXISTS reply_rows(host TEXT,topic_public_id TEXT,internal_id TEXT,public_id TEXT,parent_internal_id TEXT,raw_json TEXT,PRIMARY KEY(host,topic_public_id,internal_id));CREATE TABLE IF NOT EXISTS reply_expectations(host TEXT,topic_public_id TEXT,parent_internal_id TEXT,expected INTEGER,PRIMARY KEY(host,topic_public_id,parent_internal_id));CREATE TABLE IF NOT EXISTS known_topic_types(host TEXT,topic_public_id TEXT,content_type TEXT,PRIMARY KEY(host,topic_public_id));CREATE TABLE IF NOT EXISTS reply_observations(task_id TEXT,host TEXT,topic_public_id TEXT,internal_id TEXT,public_id TEXT,parent_internal_id TEXT,raw_json TEXT,PRIMARY KEY(task_id,internal_id));CREATE TABLE IF NOT EXISTS refreshed_tasks(task_id TEXT PRIMARY KEY,reason TEXT);CREATE TABLE IF NOT EXISTS totals(host TEXT,topic_public_id TEXT,expected INTEGER,PRIMARY KEY(host,topic_public_id));''')
  # Migrate older local stores from immutable successful list receipts before any refresh.
  if not self.db.execute('SELECT 1 FROM topic_observations LIMIT 1').fetchone():
   for r in self.db.execute("SELECT * FROM receipts WHERE status=200 ORDER BY rowid").fetchall():
    if urlsplit(r['url']).path not in ['/contentNavigation/topics','/productUpdates/topics','/categoryV2/listReadableCategoryContent','/categoryV2/listReadableCategoryOrderTopics','/categoryV2/getReadableCuratedContent']:continue
    old=json.loads(gzip.decompress((self.root/r['path']).read_bytes()));request=old.get('request') or {};tid=hashlib.sha256((r['url']+json.dumps(request,sort_keys=True)).encode()).hexdigest();host=urlsplit(r['url']).hostname
    self.db.execute('DELETE FROM topic_observations WHERE task_id=?',(tid,))
    topics=old['response'].get('topics',old['response'].get('result',{}).get('topics',[]))
    if isinstance(topics,dict):topics=[t for v in topics.values() for t in v]
    for topic in topics:self.db.execute('INSERT OR REPLACE INTO topic_observations VALUES(?,?,?,?)',(tid,host,str(topic['publicId']),json.dumps(topic,ensure_ascii=False)))
   self.db.commit()
 def task(self,url,payload,kind,method='POST'):
  validate(url,method,payload);raw=json.dumps(payload,sort_keys=True);i=hashlib.sha256((url+raw).encode()).hexdigest();self.db.execute('INSERT OR IGNORE INTO tasks(id,url,request,kind) VALUES(?,?,?,?)',(i,url,raw,kind));self.db.execute("UPDATE tasks SET state='pending' WHERE id=? AND state='stale'",(i,));return i
 def ingest(self,e,topic_type=None):
  p=validate(e['url'],e['method'],e.get('request') or {});payload=e.get('request') or {};body=e['response'];raw=json.dumps(e,sort_keys=True,ensure_ascii=False).encode();sha=hashlib.sha256(raw).hexdigest();path=self.root/'json'/sha[:2]/(sha+'.json.gz');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(gzip.compress(raw,mtime=0))
  with self.db:
   old=self.db.execute('SELECT id FROM receipts WHERE url=? AND request=? AND status=200',(e['url'],json.dumps(payload,sort_keys=True))).fetchall()
   self.db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?,?)',(sha,e['url'],e['method'],json.dumps(payload,sort_keys=True),e['status'],str(path.relative_to(self.root)),NOW()))
   tid=self.task(e['url'],payload,'observed',e['method'])
   if old and sha not in {r[0] for r in old}:self.db.execute('INSERT OR REPLACE INTO refreshed_tasks VALUES(?,?)',(tid,'changed API response snapshot; deletions/moves not globally reconciled'))
   self.db.execute('UPDATE tasks SET state=? WHERE id=?',('captured' if e['status']==200 else 'failed',tid))
   if e['status']!=200:return
   if p.path in ['/contentNavigation/topics','/productUpdates/topics'] or p.path.startswith('/categoryV2/') and 'result'in body:
    body_topics=body.get('topics',body.get('result',{}).get('topics',[]))
    if isinstance(body_topics,dict):body_topics=[t for topics in body_topics.values() for t in topics]
    self.db.execute('DELETE FROM topic_observations WHERE task_id=?',(tid,))
    for t in body_topics:self.db.execute('INSERT OR REPLACE INTO topic_observations VALUES(?,?,?,?)',(tid,p.hostname,str(t['publicId']),json.dumps(t,ensure_ascii=False)))
    self.db.execute('DELETE FROM topic_rows WHERE host=?',(p.hostname,))
    self.db.execute('INSERT OR REPLACE INTO topic_rows SELECT host,public_id,raw_json FROM topic_observations WHERE host=? ORDER BY rowid',(p.hostname,))
   if body.get('topics'):
    for t in body['topics']:
     if t.get('contentType'):self.db.execute('INSERT OR REPLACE INTO known_topic_types VALUES(?,?,?)',(p.hostname,str(t['publicId']),t['contentType']))
   if p.path in ['/categoryV2/listReadableCategoryContent','/categoryV2/listReadableCategoryOrderTopics','/categoryV2/getReadableCuratedContent']:
    for t in body_topics:
     if t.get('contentType'):self.db.execute('INSERT OR REPLACE INTO known_topic_types VALUES(?,?,?)',(p.hostname,str(t['publicId']),t['contentType']))
    if p.path=='/categoryV2/listReadableCategoryContent':
     pag=payload['pagination']
     more=pag['page']*pag['pageSize']<body['result']['total']
     if more:
      nextbody=json.loads(json.dumps(payload));nextbody['pagination']['page']+=1;self.task(e['url'],nextbody,'knowledge-base-continuation')
   elif p.path=='/productUpdates/topics':
    page=int(dict(parse_qsl(p.query)).get('page',1))
    if page<body['totalPageCount']:
     q=dict(parse_qsl(p.query));q['page']=str(page+1);self.task(urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),'')),{},'product-update-continuation','GET')
   elif p.path=='/events/events':
    self.db.execute('CREATE TABLE IF NOT EXISTS event_pages(url TEXT PRIMARY KEY,raw_json TEXT)');self.db.execute('INSERT OR REPLACE INTO event_pages VALUES(?,?)',(e['url'],json.dumps(body,ensure_ascii=False)))
    page=int(dict(parse_qsl(p.query)).get('page',1))
    for tab,key in [('past','totalPageCountPast'),('upcoming','totalPageCountUpcoming')]:
     if page<body.get(key,0):
      q=dict(parse_qsl(p.query));q.update(page=str(page+1),tab=tab);self.task(urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),'')),{},'event-continuation','GET')
   elif p.path=='/contentNavigation/topics':
    pag=payload['pagination'];page=pag['page'];size=pag['pageSize']
    if page*size<min(body['total'],10000):
     nextbody=json.loads(json.dumps(payload));nextbody['pagination']['page']+=1;self.task(e['url'],nextbody,'topic-list-continuation')
   elif '/replies' in p.path:
    topic=re.search(r'/topic/(\d+)/',p.path)[1];parent=str(payload['parentId']['id']) if payload.get('parentId') else '';typ=topic_type or (payload.get('parentId') or {}).get('contentType')
    if old and sha not in {r[0] for r in old} and not parent and payload.get('offset')==0:
     # A new root generation cannot reuse old continuation pages or child expectations.
     self.db.execute('DELETE FROM reply_observations WHERE host=? AND topic_public_id=?',(p.hostname,topic))
     self.db.execute('DELETE FROM reply_expectations WHERE host=? AND topic_public_id=?',(p.hostname,topic))
     self.db.execute("UPDATE tasks SET state='stale' WHERE url=? AND id<>?",(e['url'],tid))
    self.db.execute('INSERT OR REPLACE INTO totals VALUES(?,?,?)',(p.hostname,topic,body['topicReplyCount']))
    self.db.execute('DELETE FROM reply_observations WHERE task_id=?',(tid,))
    def rows(container,parent):
     meta=container['metadata'];self.db.execute('INSERT OR REPLACE INTO reply_expectations VALUES(?,?,?,?)',(p.hostname,topic,parent,meta['totalCount']))
     for r in container['result']:
      self.db.execute('INSERT OR REPLACE INTO reply_observations VALUES(?,?,?,?,?,?,?)',(tid,p.hostname,topic,str(r['id']),str(r['publicReplyId']),parent or None,json.dumps(r,ensure_ascii=False)))
      if r.get('children'):rows(r['children'],str(r['id']))
     if meta.get('hasMore'):
      if not typ:raise ValueError('verified topic contentType needed for continuation')
      nextbody={'offset':meta['offset']+len(container['result']),'pageSize':20,'sort':'oldestFirst'}
      if parent:nextbody['parentId']={'id':int(parent),'contentType':typ}
      self.task(e['url'],nextbody,'nested-continuation' if parent else 'reply-continuation')
    rows(body['replies'],parent)
    self.db.execute('DELETE FROM reply_rows WHERE host=? AND topic_public_id=?',(p.hostname,topic))
    self.db.execute('INSERT OR REPLACE INTO reply_rows SELECT host,topic_public_id,internal_id,public_id,parent_internal_id,raw_json FROM reply_observations WHERE host=? AND topic_public_id=? ORDER BY rowid',(p.hostname,topic))
 def plan_replies(self,host,public_id):
  row=self.db.execute('SELECT raw_json FROM topic_rows WHERE host=? AND public_id=?',(host,str(public_id))).fetchone()
  if not row:raise ValueError('topic identity must come from captured public list')
  topic=json.loads(row[0]);typ=topic.get('contentType')
  if not typ:raise ValueError('topic type not captured')
  with self.db:
   self.db.execute('INSERT OR REPLACE INTO known_topic_types VALUES(?,?,?)',(host,str(public_id),typ))
   return self.task('https://'+host+'/topic/'+str(public_id)+'/replies',{'offset':0,'pageSize':20,'sort':'oldestFirst'},'topic-replies')
 def report(self):
  topics=[]
  changed=self.db.execute('SELECT count(*) FROM refreshed_tasks').fetchone()[0]
  for t in self.db.execute('SELECT * FROM totals'):
   rows=list(self.db.execute('SELECT * FROM reply_rows WHERE host=? AND topic_public_id=?',(t['host'],t['topic_public_id'])));ids={r['internal_id'] for r in rows};dangling=[r['internal_id'] for r in rows if r['parent_internal_id'] and r['parent_internal_id'] not in ids];expect=[]
   for e in self.db.execute('SELECT * FROM reply_expectations WHERE host=? AND topic_public_id=?',(t['host'],t['topic_public_id'])):
    actual=sum((r['parent_internal_id'] or '')==e['parent_internal_id'] for r in rows)
    if actual!=e['expected']:expect.append({'parent':e['parent_internal_id'],'captured':actual,'expected':e['expected']})
   topics.append({'host':t['host'],'topic':t['topic_public_id'],'captured':len(rows),'expected':t['expected'],'dangling_parents':dangling,'count_gaps':expect,'count_consistent':len(rows)==t['expected'] and not dangling and not expect,'refresh_reconciled':not changed})
  return {'changed_snapshot_tasks':changed,'receipts':self.db.execute('SELECT count(*) FROM receipts').fetchone()[0],'topic_rows':self.db.execute('SELECT count(*) FROM topic_rows').fetchone()[0],'tasks':{r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM tasks GROUP BY state')},'reply_audits':topics,'complete':False,'limits':['public categories/groups/KB/curation/updates/events supported; ideas denied route excluded','10000listing cap uses evidenced type partitions; ID union/secondpass reconciliation still required','anonymous HTTP bootstrap validated EN/DE/NL; private/hidden/deleted content not claimed','coverage is bounded by captured receipts; check controller phase/queue, never infer global completion']}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--topic-type');a=p.parse_args();store=APIStore(a.root);data=unwrap(json.loads(a.evidence.read_text()));entries=data if isinstance(data,list) else [data] if 'url'in data else list(data.values())
 for e in entries:store.ingest(e,a.topic_type)
 print(json.dumps(store.report(),indent=2))
