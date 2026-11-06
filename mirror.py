#!/usr/bin/env python3
"""Private public-page mirror. Collection only, no keyword analysis or search index."""
import argparse,contextlib,datetime,gzip,hashlib,json,re,sqlite3,time,urllib.error,urllib.request,urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode
HOSTS={'en.community.sonos.com','de.community.sonos.com','nl.community.sonos.com'}
NOW=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()

def safe_url(url,base=None):
 try:
  p=urlsplit(urljoin(base or '',url));port=p.port
 except ValueError:return None
 if p.scheme!='https' or p.hostname not in HOSTS or port not in {None,443} or p.username or p.password:return None
 # Drop fragments, preserve provider pagination only; no member/profile/search/auth paths.
 if re.search(r'^/(?:members?|inbox|search|ssoproxy|oauth|badges|topic|community|auth|login|logout|post|subscribers)(?:/|$)',p.path):return None
 if not p.path.startswith('/p/') and p.path not in {'/','/groups','/ideas','/product-updates','/events'} and not re.search(r'-[0-9]+(?:/index[0-9]+\.html)?/?$',p.path):return None
 q=parse_qsl(p.query,keep_blank_values=True)
 if any(k=='postid' for k,v in q):q=[(k,v) for k,v in q if k!='postid']
 if any(k=='sort' and v!='lastActivityAt' for k,v in q):return None
 if any(k not in {'page','pageSize','offset','postid','sort'} for k,v in q):return None
 return urlunsplit(('https',p.netloc,p.path,urlencode(q),''))

class RobotsPolicy:
 def __init__(self,text):
  self.parser=urllib.robotparser.RobotFileParser();self.parser.parse(text.splitlines());self.rules=[];agents=[];rules_started=False
  for line in text.splitlines():
   line=line.split('#',1)[0].strip()
   if ':' not in line:continue
   key,value=line.split(':',1);key=key.lower().strip();value=value.strip()
   if key=='user-agent':
    if rules_started:agents=[];rules_started=False
    agents.append(value.lower())
   elif key in {'allow','disallow'} and '*' in agents:
    rules_started=True
    if value:self.rules.append((value,key=='allow'))
 def can_fetch(self,agent,url):
  p=urlsplit(url);target=p.path+('?' + p.query if p.query else '');matches=[]
  for pattern,allow in self.rules:
   end=pattern.endswith('$');pattern=pattern[:-1] if end else pattern
   regex='^'+re.escape(pattern).replace(r'\*','.*')+('$' if end else '')
   if re.search(regex,target):matches.append((len(pattern.replace('*','')),allow))
  return max(matches,default=(0,True))[1]
 def crawl_delay(self,agent):return self.parser.crawl_delay(agent)

class Page(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.components=[];self.links=set();self.next=None
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if a.get('data-props'):
   try:self.components.append((a.get('data-preact',''),json.loads(a['data-props'])))
   except json.JSONDecodeError:pass
  if tag in {'a','link'} and a.get('href'):self.links.add(a['href'])
  if tag=='link' and 'next' in a.get('rel','').split():self.next=a.get('href')

def parse_page(body,url):
 parser=Page();parser.feed(body.decode('utf-8',errors='replace'));topic_data=next((d for n,d in parser.components if 'TopicView/V2' in n),None)
 # Extract URL-valued fields only, never inspect body words or build a text index.
 def component_urls(value):
  if isinstance(value,dict):
   for k,v in value.items():
    if isinstance(v,str) and (k.lower().endswith('url') or k.lower() in {'href','link','destination'}):
     if u:=safe_url(v,url):parser.links.add(u)
    elif isinstance(v,(dict,list)):component_urls(v)
  elif isinstance(value,list):
   for v in value:component_urls(v)
 for name,data in parser.components:component_urls(data)
 result={'structure':None,'topic':None,'posts':[],'gaps':[],'next':safe_url(parser.next,url) if parser.next else None,'links':sorted({u for x in parser.links if (u:=safe_url(x,url)) and u!=safe_url(url)})}
 if not topic_data:
  structure=next((d for n,d in parser.components if 'CategoryTopicList' in n),None)
  if structure is not None:
   result['structure']={'url':url,'category_id':str(structure.get('categoryId')),'page':structure.get('initialPage'),'pages_expected':structure.get('totalPageCount'),'topics_expected':structure.get('totalTopicsCount'),'topic_urls':sorted({u for t in structure.get('topics',[]) if (u:=safe_url(t.get('topicUrl',{}).get('destination',''),url))})}
   result['next']=safe_url(structure.get('nextPageUrl',''),url) if structure.get('nextPageUrl') else None
   if structure.get('totalPageCount',1)>structure.get('initialPage',1) and not result['next']:result['gaps'].append({'kind':'structure-next-page-missing-or-disallowed','url':url})
   return result
  result['gaps'].append({'kind':'unrecognized-page','url':url});return result
 d=topic_data;t=d['topic'];props=d['repliesProps'];container=props['replies'];host=urlsplit(url).hostname;key=host+':'+str(t['publicId']);canonical=safe_url(t['fullTopicUrl'])
 if isinstance(container,list):container={'result':container,'metadata':{'totalCount':t.get('replyCount',props.get('totalReplies',0)),'hasMore':int(d.get('currentPage') or 1)<max(1,int(props.get('totalPages') or 1))}}
 meta=container['metadata']
 result['topic']={'key':key,'url':canonical,'title':t.get('title'),'public_id':str(t['publicId']),'private_id':str(t['id']),'content_type':t['contentType'],'top_expected':int(meta['totalCount']),'all_expected':props.get('totalReplies'),'pages_expected':max(1,int(props.get('totalPages') or 1)),'page':int(d.get('currentPage') or 1)}
 def post(x,parent=None,kind='reply'):
  item={k:x.get(k) for k in ['id','publicReplyId','title','content','publishedAt','repliedAt','url','attachments','authorId'] if k in x};item.update(topic_key=key,parent=parent,kind=kind);result['posts'].append(item)
  child=x.get('children');expected=x.get('inclusiveVisibleReplyCount',0)
  if child:
   cm=child.get('metadata',{});children=child.get('result',[])
   for c in children:post(c,str(x['id']))
   if cm.get('hasMore') or cm.get('hasPrevious') or len(children)<cm.get('totalCount',len(children)):
    result['gaps'].append({'kind':'nested-replies-incomplete','parent':str(x['id']),'expected':cm.get('totalCount'),'captured':len(children),'offset':cm.get('offset'),'topic':canonical})
  elif expected:result['gaps'].append({'kind':'nested-replies-not-embedded','parent':str(x['id']),'expected':expected,'topic':canonical})
 post(t,kind='topic')
 for x in container['result']:post(x)
 # Pins/best answers may repeat; DB deduplicates stable IDs.
 for name in ['bestAnswer','pinnedReply']:
  x=d.get(name)
  if isinstance(x,dict) and x.get('id') and x.get('content'):post(x)
 if not result['next'] and meta.get('nextPageTopicUrl'):result['next']=safe_url(meta['nextPageTopicUrl'],url)
 if meta.get('hasMore') and not result['next']:result['gaps'].append({'kind':'pagination-link-missing','topic':canonical,'page':d.get('currentPage')})
 return result

class Mirror:
 def __init__(self,root):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(self.root/'mirror.sqlite');self.db.row_factory=sqlite3.Row
  self.db.executescript('''
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS queue(url TEXT PRIMARY KEY,kind TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,error TEXT,source TEXT);
  CREATE TABLE IF NOT EXISTS responses(url TEXT PRIMARY KEY,final_url TEXT,sha256 TEXT,status INTEGER,bytes INTEGER,captured_at TEXT,headers TEXT,path TEXT);
  CREATE TABLE IF NOT EXISTS response_history(url TEXT,sha256 TEXT,captured_at TEXT,path TEXT,PRIMARY KEY(url,sha256));
  CREATE TABLE IF NOT EXISTS parsed_snapshots(url TEXT,sha256 TEXT,captured_at TEXT,parsed_json TEXT,PRIMARY KEY(url,sha256));
  CREATE TABLE IF NOT EXISTS page_posts(url TEXT,topic_key TEXT,id TEXT,parent TEXT,kind TEXT,raw_json TEXT,PRIMARY KEY(url,kind,id));
  CREATE TABLE IF NOT EXISTS structures(url TEXT PRIMARY KEY,raw_json TEXT);
  CREATE TABLE IF NOT EXISTS topics(key TEXT PRIMARY KEY,url TEXT,title TEXT,top_expected INTEGER,all_expected INTEGER,pages_expected INTEGER);
  CREATE TABLE IF NOT EXISTS topic_pages(topic_key TEXT,page INTEGER,url TEXT,PRIMARY KEY(topic_key,page));
  CREATE TABLE IF NOT EXISTS posts(topic_key TEXT,id TEXT,parent TEXT,kind TEXT,raw_json TEXT,PRIMARY KEY(topic_key,kind,id));
  CREATE TABLE IF NOT EXISTS gaps(id TEXT PRIMARY KEY,topic_key TEXT,kind TEXT,details TEXT);
  CREATE TABLE IF NOT EXISTS links(source TEXT,url TEXT,PRIMARY KEY(source,url));
  CREATE TABLE IF NOT EXISTS host_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT);
  ''');self.db.commit()
 def enqueue(self,url,kind='discovered',source=None):
  u=safe_url(url)
  if not u:return False
  self.db.execute('INSERT OR IGNORE INTO queue(url,kind,source) VALUES(?,?,?)',(u,kind,source));return True
 def seed(self,paths):
  with self.db:
   for path in paths:
    for line in Path(path).read_text().splitlines():
     if line.strip() and not self.enqueue(line.strip(),'seed',str(path)):raise ValueError('unsafe seed '+line)
 def gap(self,key,kind,details):
  value=json.dumps(details,sort_keys=True);i=hashlib.sha256((str(key)+kind+value).encode()).hexdigest();self.db.execute('INSERT OR REPLACE INTO gaps VALUES(?,?,?,?)',(i,key,kind,value))
 def ingest(self,url,body,status=200,headers=None,final_url=None):
  final_url=final_url or url
  if not safe_url(final_url):raise ValueError('unsafe redirect')
  sha=hashlib.sha256(body).hexdigest();path=self.root/'responses'/sha[:2]/(sha+'.html.gz');path.parent.mkdir(parents=True,exist_ok=True)
  if not path.exists():
   temp=path.with_suffix('.tmp');temp.write_bytes(gzip.compress(body,mtime=0));temp.replace(path)
  try:parsed=parse_page(body,final_url)
  except (KeyError,TypeError,ValueError) as e:
   parsed={'structure':None,'topic':None,'posts':[],'gaps':[{'kind':'parse-failed','url':url,'error':str(e)}],'links':[],'next':None}
  with self.db:
   self.enqueue(url)
   self.db.execute('INSERT OR REPLACE INTO responses VALUES(?,?,?,?,?,?,?,?)',(url,final_url,sha,status,len(body),NOW(),json.dumps(headers or {}),str(path.relative_to(self.root))))
   self.db.execute('INSERT OR IGNORE INTO parsed_snapshots VALUES(?,?,?,?)',(url,sha,NOW(),json.dumps(parsed,ensure_ascii=False)))
   self.db.execute('INSERT OR IGNORE INTO response_history VALUES(?,?,?,?)',(url,sha,NOW(),str(path.relative_to(self.root))))
   if parsed.get('structure'):
    self.db.execute('INSERT OR REPLACE INTO structures VALUES(?,?)',(url,json.dumps(parsed['structure'])))
    self.db.execute("DELETE FROM gaps WHERE kind='unrecognized-page' AND details LIKE ?",('%'+url+'%',))
   t=parsed['topic'];key=t['key'] if t else None
   if self.db.execute('SELECT count(*) FROM response_history WHERE url=?',(url,)).fetchone()[0]>1:
    self.gap(key,'refreshed-page-needs-reconciliation',{'url':url,'reason':'latest page records replaced; historical snapshots retained; cross-page time consistency not proven'})
   if t:
    self.db.execute('INSERT OR REPLACE INTO topics VALUES(?,?,?,?,?,?)',(key,t['url'],t['title'],t['top_expected'],t['all_expected'],t['pages_expected']))
    self.db.execute('INSERT OR REPLACE INTO topic_pages VALUES(?,?,?)',(key,t['page'],url))
    self.db.execute('DELETE FROM page_posts WHERE url=?',(url,))
    for p in parsed['posts']:self.db.execute('INSERT OR REPLACE INTO page_posts VALUES(?,?,?,?,?,?)',(url,key,str(p['id']),p['parent'],p['kind'],json.dumps(p,ensure_ascii=False)))
    # Latest observations per page replace only that page; immutable snapshots keep history.
    self.db.execute('DELETE FROM posts WHERE topic_key=?',(key,))
    self.db.execute('INSERT OR REPLACE INTO posts SELECT topic_key,id,parent,kind,raw_json FROM page_posts WHERE topic_key=? ORDER BY rowid',(key,))
   for g in parsed['gaps']:self.gap(key,g['kind'],g)
   for link in parsed['links']:
    self.db.execute('INSERT OR IGNORE INTO links VALUES(?,?)',(url,link));self.enqueue(link,source=url)
   if parsed['next']:self.enqueue(parsed['next'],'pagination',url)
   self.db.execute("UPDATE queue SET state='captured',error=NULL WHERE url=?",(url,))
  return parsed
 def verify_originals(self):
  errors=[]
  for r in self.db.execute('SELECT url,path,sha256 FROM responses UNION SELECT url,path,sha256 FROM response_history'):
   try:
    if hashlib.sha256(gzip.decompress((self.root/r['path']).read_bytes())).hexdigest()!=r['sha256']:errors.append({'url':r['url'],'error':'hash mismatch'})
   except Exception as e:errors.append({'url':r['url'],'error':str(e)})
  return errors
 def recover(self):
  with self.db:self.db.execute("UPDATE queue SET state='pending' WHERE state='fetching'")
 def stop(self,host,reason):
  with self.db:self.db.execute('INSERT OR REPLACE INTO host_stops VALUES(?,?,?)',(host,reason,NOW()))
 def run(self,max_pages,robots,delay=5,max_bytes=3000000,fetch=None):
  if max_pages<1 or max_pages>25:raise ValueError('Validation cap is 1..25 pages; full crawl needs a separate operational decision')
  count=0
  while count<max_pages:
   stopped={r[0] for r in self.db.execute('SELECT host FROM host_stops')}
   row=next((r for r in self.db.execute("SELECT * FROM queue WHERE state='pending' ORDER BY rowid") if urlsplit(r['url']).hostname not in stopped),None)
   if not row:break
   url=row['url'];host=urlsplit(url).hostname
   if self.db.execute('SELECT 1 FROM host_stops WHERE host=?',(host,)).fetchone():break
   robot=robots.get(host)
   if not robot or not robot.can_fetch('*',url):
    with self.db:self.db.execute("UPDATE queue SET state='excluded',error='robots disallow or missing policy' WHERE url=?",(url,))
    continue
   with self.db:self.db.execute("UPDATE queue SET state='fetching',attempts=attempts+1 WHERE url=?",(url,))
   count+=1
   try:
    if fetch:body,final,headers=fetch(url)
    else:
     class Guard(urllib.request.HTTPRedirectHandler):
      def redirect_request(self,req,fp,code,msg,hs,newurl):
       if not safe_url(newurl) or urlsplit(newurl).hostname!=host or not robot.can_fetch('*',newurl):raise ValueError('redirect outside allowed public scope')
       return super().redirect_request(req,fp,code,msg,hs,newurl)
     with urllib.request.build_opener(Guard()).open(urllib.request.Request(url,headers={'User-Agent':'sonos-public-mirror/0.1 (bounded private preservation validation)'}),timeout=25) as r:
      body=r.read(max_bytes+1);final=r.url;headers={k:r.headers.get(k) for k in ['Date','ETag','Last-Modified','Content-Type']}
    if len(body)>max_bytes:raise ValueError('response exceeds size cap')
    if any(marker in body.lower() for marker in [b'cf-chl-',b'challenge-platform',b'<title>just a moment',b'<title>access denied']):
     self.stop(host,'challenge page');raise ValueError('challenge page')
    self.ingest(url,body,headers=headers,final_url=final)
   except Exception as e:
    status=getattr(e,'code',None)
    with self.db:self.db.execute("UPDATE queue SET state='failed',error=? WHERE url=?",(str(e),url));self.gap(None,'fetch-failed',{'url':url,'error':str(e),'status':status})
    if status in {403,429}:self.stop(host,str(status));break
   wait=max(delay,robot.crawl_delay('*') or 0)
   if count<max_pages and wait:time.sleep(wait)
  return self.report()
 def report(self):
  states={r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM queue GROUP BY state')};audits=[]
  for t in self.db.execute('SELECT * FROM topics'):
   key=t['key'];pages=self.db.execute('SELECT count(*) FROM topic_pages WHERE topic_key=?',(key,)).fetchone()[0];top=self.db.execute("SELECT count(*) FROM posts WHERE topic_key=? AND kind='reply' AND parent IS NULL",(key,)).fetchone()[0];alln=self.db.execute("SELECT count(*) FROM posts WHERE topic_key=? AND kind='reply'",(key,)).fetchone()[0]
   gaps=[r[0] for r in self.db.execute('SELECT DISTINCT kind FROM gaps WHERE topic_key=?',(key,))];ok=pages==t['pages_expected'] and top==t['top_expected'] and (t['all_expected'] is not None and alln==t['all_expected']) and not gaps
   audits.append({'topic':key,'pages':[pages,t['pages_expected']],'top_level_replies':[top,t['top_expected']],'all_replies':[alln,t['all_expected']],'complete':ok,'gaps':gaps})
  browser_audits=[]
  if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='browser_snapshots'").fetchone():browser_audits=[json.loads(r[0]) for r in self.db.execute('SELECT audit_json FROM browser_snapshots ORDER BY observed_at')]
  return {'browser_expansion_audits':browser_audits,'snapshot_policy':'immutable per-URL content snapshots plus latest observed page/post projection; not a globally simultaneous snapshot','snapshot_total':self.db.execute('SELECT count(*) FROM parsed_snapshots').fetchone()[0],'queue':states,'structure_pages':[json.loads(r[0]) for r in self.db.execute('SELECT raw_json FROM structures')],'frontier_by_host_kind_state':[{'host':r[0],'kind':r[1],'state':r[2],'count':r[3]} for r in self.db.execute("SELECT substr(url,9,instr(substr(url,9),'/')-1),kind,state,count(*) FROM queue GROUP BY 1,2,3")],'topics':audits,'host_stops':[dict(r) for r in self.db.execute('SELECT * FROM host_stops')],'complete':False,'coverage':'bounded public capture only; per-topic complete means embedded-count consistency at capture, not independent exhaustiveness; no global completeness claim, no keyword/text scanning','limits':['raw nested request got403; ordinary anonymous UI expansion verified for one topic; browser expansion not automated across frontier','attachment and inline media bytes not downloaded; original URLs preserved','category HTML next-page schema supported; other structures and complete traversal not validated','public indexed/discovered content only; private/deleted/unindexed content not guaranteed','latest-page records remove stale local posts, but cross-page time consistency and moves remain unproven; immutable capture snapshots retained']}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);sub=p.add_subparsers(dest='cmd',required=True)
 s=sub.add_parser('seed');s.add_argument('files',nargs='+');sub.add_parser('report');sub.add_parser('verify');s=sub.add_parser('capture');s.add_argument('--max-pages',type=int,required=True);s.add_argument('--robots-dir',type=Path,required=True);s.add_argument('--delay',type=float,default=5)
 a=p.parse_args();m=Mirror(a.root)
 if a.cmd=='verify':
  errors=m.verify_originals();print(json.dumps({'original_errors':errors},indent=2));raise SystemExit(bool(errors))
 if a.cmd=='seed':m.seed(a.files)
 if a.cmd=='capture':
  if a.delay<1.5:p.error('minimum spacing is 1.5s')
  policies={}
  for host in HOSTS:
   path=a.robots_dir/(host+'.txt')
   if path.exists():policies[host]=RobotsPolicy(path.read_text())
  m.recover();print(json.dumps(m.run(a.max_pages,policies,delay=a.delay),indent=2))
 else:print(json.dumps(m.report(),indent=2))
if __name__=='__main__':main()
