"""Bounded public API runner with ordinary anonymous session, durable tasks and stop state."""
import argparse,http.cookiejar,json,re,time,urllib.error,urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from html.parser import HTMLParser
from api_adapter import APIStore,validate
from mirror import HOSTS,RobotsPolicy,NOW
class Token(HTMLParser):
 def __init__(self):super().__init__();self.token=None
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='input' and a.get('id')=='csrftoken':self.token=a.get('value')
class Stop(Exception):pass
class Session:
 def __init__(self,host,root):
  if host not in HOSTS:raise ValueError('unsupported host')
  self.host=host;self.root=Path(root);self.jar=http.cookiejar.CookieJar()
  class Guard(urllib.request.HTTPRedirectHandler):
   def redirect_request(inner,req,fp,code,msg,headers,newurl):
    if urlsplit(newurl).hostname!=host:raise Stop('redirect outside expected public host')
    return super().redirect_request(req,fp,code,msg,headers,newurl)
  self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),Guard());self.token=None;self.policy=None
 def request(self,url,payload=None):
  for attempt in range(3):
   try:return self._request_once(url,payload)
   except OSError:
    self.evidence({'url':url,'stage':'idempotent transport retry','attempt':attempt+1})
    if attempt==2:raise
    time.sleep(5*(attempt+1))
 def _request_once(self,url,payload=None):
  if urlsplit(url).hostname!=self.host:raise ValueError('wrong host')
  h={'User-Agent':'sonos-public-mirror/0.2 (bounded public preservation)','Accept':'application/json' if payload is not None else 'text/html'}
  if self.token:h.update({'X-CSRF-TOKEN':self.token,'X-Requested-With':'XMLHttpRequest'})
  data=None if payload is None else json.dumps(payload).encode()
  if data is not None:h['Content-Type']='application/json'
  try:
   with self.opener.open(urllib.request.Request(url,headers=h,data=data),timeout=25) as r:
    if urlsplit(r.url).hostname!=self.host:raise Stop('redirect left expected host')
    body=r.read(3000001);status=r.status;ctype=r.headers.get('Content-Type','')
  except urllib.error.HTTPError as e:
   # No credentials or request headers retained.
   body=e.read(2000);self.evidence({'url':url,'status':e.code,'body_preview':body.decode(errors='replace')});raise Stop('HTTP '+str(e.code))
  if len(body)>3000000:raise Stop('response too large')
  if any(m in body.lower() for m in [b'cf-chl-',b'challenge-platform',b'<title>access denied',b'<title>just a moment']):raise Stop('challenge page')
  return body,ctype,status
 def evidence(self,data):
  p=self.root/'transport-events.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
  with p.open('a') as f:f.write(json.dumps(dict(data,at=NOW()))+'\n')
 def bootstrap(self):
  robots,_,_=self.request('https://'+self.host+'/robots.txt');self.policy=RobotsPolicy(robots.decode());home='https://'+self.host+'/'
  if not self.policy.can_fetch('*',home):raise Stop('robots excludes bootstrap')
  time.sleep(max(1,self.policy.crawl_delay('*') or 0));body,_,_=self.request(home);p=Token();p.feed(body.decode(errors='replace'));self.token=p.token
  if not self.token:raise Stop('anonymous CSRF bootstrap not found')
  self.evidence({'host':self.host,'stage':'bootstrap','status':'ok','token_found':True})
 def read(self,url,payload):
  from group_discovery import group_path,parse_group_page
  method='GET' if group_path(urlsplit(url).path) or urlsplit(url).path in ['/productUpdates/topics','/events/events'] else 'POST';validate(url,method,payload)
  if not self.policy.can_fetch('*',url):raise Stop('robots excludes API task')
  body,ctype,status=self.request(url,payload if method=='POST' else None)
  if group_path(urlsplit(url).path):
   if 'text/html' not in ctype:raise Stop('group page not HTML')
   return {'url':url,'method':'GET','request':{},'status':status,'response':parse_group_page(body,url)}
  if 'application/json' not in ctype:raise Stop('API returned non-JSON')
  return {'url':url,'method':method,'request':payload,'status':status,'response':json.loads(body)}
def run(root,host,limit,delay=1,session_factory=Session):
 if delay<1:raise ValueError('minimum delay1second')
 if not 1<=limit<=10:raise ValueError('bounded validation cap1..10, no full crawl')
 store=APIStore(root);store.db.execute('CREATE TABLE IF NOT EXISTS transport_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT)');store.db.commit()
 if store.db.execute('SELECT 1 FROM transport_stops WHERE host=?',(host,)).fetchone():raise Stop('host has persistent stop; review evidence first')
 session=session_factory(host,root);done=0
 try:
  session.bootstrap()
  time.sleep(max(delay,session.policy.crawl_delay('*') or 0) if getattr(session,'policy',None) else delay)
  for i in range(limit):
   row=next((r for r in store.db.execute("SELECT * FROM tasks WHERE state='pending' ORDER BY rowid") if urlsplit(r['url']).hostname==host),None)
   if not row:break
   e=session.read(row['url'],json.loads(row['request']));typ=None
   if '/topic/' in row['url']:
    public_id=re.search(r'/topic/(\d+)/',row['url'])[1];topic=store.db.execute('SELECT content_type FROM known_topic_types WHERE host=? AND topic_public_id=?',(host,public_id)).fetchone()
    if topic:typ=topic[0]
   store.ingest(e,topic_type=typ);done+=1
   if i+1<limit:time.sleep(max(delay,session.policy.crawl_delay('*') or 0) if getattr(session,'policy',None) else delay)
 except (Stop,OSError,ValueError) as e:
  with store.db:store.db.execute('INSERT OR REPLACE INTO transport_stops VALUES(?,?,?)',(host,str(e),NOW()))
  return {'captured_this_run':done,'stopped':str(e),'report':store.report()}
 return {'captured_this_run':done,'report':store.report()}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--host',choices=sorted(HOSTS),required=True);p.add_argument('--max-requests',type=int,required=True);a=p.parse_args();print(json.dumps(run(a.root,a.host,a.max_requests),indent=2))
