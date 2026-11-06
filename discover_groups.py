"""One-shot public group index bootstrap; JSON thereafter, no accounts or membership actions."""
import argparse,json,time
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from inspect_surfaces import Props
from run_api import Session
from mirror import HOSTS

def group_rows(html,host):
 p=Props();p.feed(html);rows={}
 for r in p.rows:
  for group in r['props'].get('groups',{}).get('otherGroups',[]):
   if urlsplit(group.get('url','')).hostname!=host or not group.get('url','').startswith('https://'+host+'/groups/'):raise ValueError('invalid group origin')
   if group.get('type')!=4:continue # Do not follow private/unknown group classes.
   rows[str(group['id'])]=group
 return list(rows.values())
def discover(root,host):
 if host not in HOSTS:raise ValueError('unsupported host')
 root=Path(root);root.mkdir(parents=True,exist_ok=True);session=Session(host,root);session.bootstrap();time.sleep(max(5,session.policy.crawl_delay('*') or 0));url='https://'+host+'/groups'
 if not session.policy.can_fetch('*',url):raise ValueError('robots excludes group discovery')
 html,ctype,status=session.request(url);groups=group_rows(html.decode(),host)
 # Component supplies exact IDs expected by the observed getGroups contract.
 request_url='https://'+host+'/getGroups?'+urlencode([('ids[]',str(g['id'])) for g in groups])
 if groups:
  if not session.policy.can_fetch('*',request_url):raise ValueError('robots excludes group metadata')
  time.sleep(max(5,session.policy.crawl_delay('*') or 0));body,ctype,status=session.request(request_url)
  if 'application/json' not in ctype:raise ValueError('group API not JSON')
  data=json.loads(body);public=[g for g in data.get('otherGroups',[]) if str(g.get('id')) in {str(r['id']) for r in groups} and g.get('type')==4]
  if {str(g['id']) for g in public}!={str(g['id']) for g in groups}:raise ValueError('group index/API ID mismatch')
 else:public=[]
 # Deliberately persist public group metadata only; no page CSRF/cookies/raw HTML.
 out={'source_url':url,'api_url':request_url,'groups':public,'complete':False,'scope':'anonymous index visible public groups, not private/hidden groups'};(root/'groups.json').write_text(json.dumps(out,indent=2));return out
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--host',choices=sorted(HOSTS),required=True);a=p.parse_args();print(json.dumps(discover(a.root,a.host),indent=2))
