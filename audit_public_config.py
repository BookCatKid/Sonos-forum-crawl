"""Finite anonymous configuration audit, never follows topic-list/reply continuations."""
import argparse,json,time
from pathlib import Path
from urllib.parse import urlsplit
from inspect_surfaces import Props
from run_api import Session,Stop

def summarize(rows,url):
 out={'url':url,'components':[],'curation':[],'featured_ids':[],'topic_list':None,'public_group':None}
 def flags(obj,path=''):
  if isinstance(obj,dict):
   for k,v in obj.items():
    p=path+'.'+k
    if 'curat'in k.lower():out['curation'].append({'field':p,'value':v})
    elif isinstance(v,(dict,list)):flags(v,p)
  elif isinstance(obj,list):
   for i,v in enumerate(obj):flags(v,path+'[]')
 for row in rows:
  name=row['component'] or '';p=row['props'];out['components'].append(name)
  # Inspect configuration keys only. Never scan topic/reply body text.
  for key in ['category','group','config','curatedContent','curation','categoryList']:
   if key in p:flags(p[key],key)
  if 'curation' in name.lower() or 'CategoryListWithTopics' in name:
   out.setdefault('special_configuration',[]).append({'component':name,'props':{k:v for k,v in p.items() if k not in ['phrases','csrfToken','topics','authors','cdn']}})
  if 'GroupTopicsList'in name:
   out['public_group']={'can_see':p.get('canSee'),'visibility':p.get('group',{}).get('visibility')}
  if 'CategoryTopicList'in name or 'GroupTopicsList'in name:
   out['topic_list']={k:p.get(k) for k in ['categoryId','totalTopicsCount','totalPageCount','totalPagesCount','supportedContentTypes','requestUrl'] if k in p}
  for t in p.get('featuredTopics',[]):
   if isinstance(t,dict):out['featured_ids'].append({'public_id':str(t.get('publicId',t.get('id'))),'url':t.get('url') or t.get('topicUrl',{}).get('destination'),'content_type':t.get('contentType')})
 return out

def audit(planfile,output,cap=12):
 plan=json.loads(Path(planfile).read_text());outpath=Path(output);outpath.mkdir(parents=True,exist_ok=True);statefile=outpath/'audit.json';state=json.loads(statefile.read_text()) if statefile.exists() else {'pages':{},'stopped':None}
 if state['stopped']:return state
 pending=[c for c in plan['categories'] if not c['container'] and c['url'] not in state['pages']];sessions={};done=0
 for c in pending[:cap]:
  host=c['host'];url=c['url']
  try:
   if host not in sessions:
    session=Session(host,outpath/host);session.bootstrap();sessions[host]=session
   session=sessions[host];time.sleep(max(5,session.policy.crawl_delay('*') or 0))
   if not session.policy.can_fetch('*',url):raise Stop('robots excludes configuration page')
   body,ctype,status=session.request(url)
   if 'text/html' not in ctype:raise Stop('configuration page not HTML')
   props=Props();props.feed(body.decode(errors='replace'));state['pages'][url]=dict(summarize(props.rows,url),status=status,surface=c['surface']);done+=1
  except (Stop,OSError,ValueError) as e:
   state['stopped']={'url':url,'reason':str(e)}
  statefile.write_text(json.dumps(state,indent=2))
  if state['stopped']:break
 print(json.dumps({'captured_this_batch':done,'captured':len(state['pages']),'planned':len([c for c in plan['categories'] if not c['container']]),'stopped':state['stopped']}))
 return state
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--output',required=True);p.add_argument('--cap',type=int,default=12);a=p.parse_args();audit(a.plan,a.output,a.cap)
