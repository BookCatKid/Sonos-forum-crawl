"""Finite config-only audit. Never persists post bodies or complete props."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from finite_discovery import Discovery
from inspect_surfaces import Props
from run_api import Stop
from build_discovery_plan import build
from integrate_config import integrate

def config(rows,url):
 out={'url':url,'components':[r['component'] for r in rows],'featured_ids':[],'public_group':None,'topic_list':None}
 for r in rows:
  name=r['component'] or '';p=r['props']
  if 'GroupTopicsList' in name:
   g=p.get('group',{});out['public_group']={'can_see':p.get('canSee'),'visibility':g.get('visibility')}
  if 'CategoryTopicList' in name or 'GroupTopicsList' in name:
   out['topic_list']={k:p[k] for k in ('categoryId','totalTopicsCount','totalPageCount','totalPagesCount','supportedContentTypes','requestUrl') if k in p}
  # No arbitrary nested props, topic content, authors, token or profile fields.
 return out

def audit(root,cap=12):
 d=Discovery(root);root=d.root;plan=json.loads((root/'discovery-plan.json').read_text());pending={c['url']:c for c in plan['categories'] if not c['container']}
 for f in root.glob('*-groups.json'):
  for g in json.loads(f.read_text())['groups']:pending[g['url']]={'host':urlsplit(g['url']).hostname,'id':g['id'],'surface':'group','url':g['url']}
 done=0
 try:
  for url,c in pending.items():
   if url in d.state['pages']:continue
   if done>=cap:break
   body,ctype,status=d.read(url)
   if 'text/html' not in ctype:raise Stop('configuration page not HTML')
   p=Props();p.feed(body.decode(errors='replace'));out=config(p.rows,url);out.update(status=status,surface=c.get('surface','category'))
   if '/groups/' in url:
    flags=out['public_group']
    if not flags or flags['can_see'] is not True or flags['visibility']!='public':raise Stop('group visibility not explicitly public')
   d.state['pages'][url]=out;done+=1;d.save()
 except (Stop,OSError,ValueError) as e:
  from mirror import NOW
  d.state['stopped']={'url':url,'kind':type(e).__name__,'reason':str(e),'at':NOW()};d.save();raise
 # Exact group IDs/counts come from the index getGroups response after each
 # detail page proved anonymous visibility. Never infer a category ID from slug.
 group_nodes=[]
 for f in root.glob('*-group-metadata.json'):
  for g in json.loads(f.read_text())['groups']:
   page=d.state['pages'].get(g['url'])
   if page and page['public_group']=={'can_see':True,'visibility':'public'}:group_nodes.append(g)
 (root/'group-nodes.json').write_text(json.dumps(group_nodes,indent=2))
 sources=list(root.glob('*-menu.json'))+list(root.glob('*-tree.json'))+[root/'group-nodes.json'];plan=build(sources);plan=integrate(plan,[{'pages':d.state['pages'],'stopped':d.state['stopped']}]);(root/'discovery-plan.json').write_text(json.dumps(plan,indent=2))
 print(json.dumps({'this_batch':done,'pages':len(d.state['pages']),'planned':len(pending),'requests':d.state['requests'],'stopped':d.state['stopped'],'categories':len(plan['categories']),'extra_contracts':sum(len(c.get('additional_reads',[])) for c in plan['categories'])}))
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--cap',type=int,default=12);a=p.parse_args();audit(a.root,a.cap)
