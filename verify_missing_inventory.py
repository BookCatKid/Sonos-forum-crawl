"""Finite ordinary public-page checks for exact sitemap differences, no reply expansion."""
import hashlib,json,time
from pathlib import Path
from urllib.parse import urlsplit
from mirror import Page,NOW,safe_url
from run_api import Session,Stop

def verify(urls,root):
 root=Path(root);root.mkdir(parents=True,exist_ok=True);sessions={}
 result_path=root/'results.json';out=json.loads(result_path.read_text()) if result_path.exists() else []
 # Recover historical denials as well as stops written by the current version.
 stops_path=root/'stops.json';stops=json.loads(stops_path.read_text()) if stops_path.exists() else {}
 for item in out:
  if item.get('error') and item['error']!='HTTP 404':stops.setdefault(urlsplit(item['url']).hostname,item['error'])
 if stops:stops_path.write_text(json.dumps(stops,indent=2))
 stopped=set(stops);seen={item['url'] for item in out}
 for url in urls:
  host=urlsplit(url).hostname
  if host in stopped or url in seen:continue
  if safe_url(url)!=url:raise ValueError('unsafe inventory URL')
  try:
   if host not in sessions:
    time.sleep(5);s=Session(host,root);s.bootstrap();sessions[host]=s
   s=sessions[host]
   if not s.policy.can_fetch('*',url):raise Stop('robots excludes exact inventory URL')
   time.sleep(max(5,s.policy.crawl_delay('*') or 0));body,ctype,status=s.request(url)
   p=Page();p.feed(body.decode(errors='replace'));d=next((d for n,d in p.components if 'TopicView/V2'in n),None)
   item={'url':url,'observed_at':NOW(),'status':status,'body_sha256':hashlib.sha256(body).hexdigest(),'topic':d.get('topic') if d else None,'components':[n for n,_ in p.components]}
  except (Stop,OSError,ValueError) as e:
   item={'url':url,'observed_at':NOW(),'error':str(e)}
   if str(e)!='HTTP 404':
    stopped.add(host);stops[host]=str(e);stops_path.write_text(json.dumps(stops,indent=2))
  out.append(item);seen.add(url);result_path.write_text(json.dumps(out,indent=2))
 return out
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--urls',required=True);p.add_argument('--root',required=True);a=p.parse_args();r=verify(json.loads(Path(a.urls).read_text()),a.root)
 for i in r:print(i['url'],i.get('status',i.get('error')),str((i.get('topic')or{}).get('publicId')))
