"""Finite anonymous configuration grounding. No topic/reply task traversal."""
import json,time
from pathlib import Path
from urllib.parse import urlsplit,urlencode
from run_api import Session,Stop
from mirror import HOSTS,NOW
from inspect_surfaces import Props
from discover_groups import group_rows
from public_payload import project,fields
from build_discovery_plan import build

class Discovery:
 def __init__(self,root):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.path=self.root/'state.json';self.state=json.loads(self.path.read_text()) if self.path.exists() else {'version':1,'pages':{},'hosts':{},'requests':0,'request_cap':120,'stopped':None,'started_at':NOW()};self.sessions={}
 def save(self):self.path.write_text(json.dumps(self.state,indent=2))
 def get_session(self,host):
  if self.state['stopped']:raise Stop('discovery has a persistent stop')
  if host not in self.sessions:
   session=Session(host,self.root/'private-transport'/host);session.bootstrap();self.sessions[host]=session;self.state['requests']+=2;self.save()
  return self.sessions[host]
 def read(self,url,payload=None):
  s=self.get_session(urlsplit(url).hostname)
  if not s.policy.can_fetch('*',url):raise Stop('robots excludes discovery resource')
  if self.state['requests']>=self.state.get('request_cap',120):raise Stop('finite discovery request cap reached')
  time.sleep(max(1,s.policy.crawl_delay('*') or 0));self.state['requests']+=1;self.save();return s.request(url,payload)
 def bootstrap(self):
  try:
   for host in sorted(HOSTS):
    if self.state['hosts'].get(host,{}).get('finished'):continue
    home='https://'+host+'/';body,ctype,status=self.read(home);p=Props();p.feed(body.decode());nodes=[]
    def walk(x):
     if isinstance(x,dict):
      if isinstance(x.get('url'),str) and urlsplit(x['url']).hostname==host and 'isContainer' in x and 'id' in x:nodes.append(x)
      for k,v in x.items():
       if isinstance(v,(dict,list)) and k not in {'topics','authors','featuredTopics','phrases'}:walk(v)
     elif isinstance(x,list):
      for v in x:walk(v)
    for r in p.rows:
     if r['component']=='mega-menu/index' or 'CategoryList' in (r['component'] or ''):walk(r['props'])
    # Project through exact category schema before persistence.
    from public_payload import category
    menus=[category(x) for x in nodes]
    (self.root/(host+'-menu.json')).write_text(json.dumps({'source_url':home,'0':menus},indent=2))
    url='https://'+host+'/categoryV2/getAccessibleCategoriesTree';payload={'excludeFields':['description','thumbnailImage'],'type':[0]};body,ctype,status=self.read(url,payload)
    if 'application/json' not in ctype:raise Stop('tree not JSON')
    e=project({'url':url,'method':'POST','request':payload,'status':status,'response':json.loads(body)});(self.root/(host+'-tree.json')).write_text(json.dumps(e,indent=2))
    url='https://'+host+'/groups';body,ctype,status=self.read(url);groups=group_rows(body.decode(),host);safe=[]
    for g in groups:
     # Minimal index metadata, no membership/account fields or group post bodies.
     safe.append({k:g[k] for k in ('id','url','type') if k in g})
    (self.root/(host+'-groups.json')).write_text(json.dumps({'source_url':url,'groups':safe},indent=2));self.state['hosts'][host]={'finished':True,'groups':len(safe),'menu_nodes':len(menus)};self.save()
  except (Stop,OSError,ValueError) as e:
   self.state['stopped']={'kind':type(e).__name__,'reason':str(e),'at':NOW()};self.save();raise
  files=list(self.root.glob('*-menu.json'))+list(self.root.glob('*-tree.json'));plan=build(files)
  self.state['plan_categories']=len(plan['categories']);self.save();(self.root/'discovery-plan.json').write_text(json.dumps(plan,indent=2));return self.state
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();d=Discovery(a.root);print(json.dumps(d.bootstrap(),indent=2))
