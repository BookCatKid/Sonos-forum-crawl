"""Offline plan only. Never enqueue or fetch a full category traversal."""
import argparse,gzip,json
from pathlib import Path
from api_adapter import unwrap

def plan(tree,cap=10000):
 output=[]
 def node(x):
  count=x.get('visibleTopicsCount');children=x.get('children') or [];is_container=bool(x.get('isContainer'))
  entry={'id':x['id'],'url':x.get('url'),'visible_count':count,'container':is_container,'status':'partition-into-children' if is_container else 'bounded-category-plan','requires_further_partition':not is_container and (count is None or count>=cap),'child_ids':[v['id'] for v in children]}
  if is_container:entry['child_count_sum']=sum(v.get('visibleTopicsCount',0) for v in children);entry['unexplained_count_delta']=count-entry['child_count_sum'] if count is not None else None
  output.append(entry)
  for c in children:node(c)
 for roots in tree.values():
  if isinstance(roots,list):
   for root in roots:node(root)
 return {'categories':output,'tasks_started':0,'complete':False,'notes':['Plan only, no crawling started','Partition containers into evidenced leaf categories to avoid global10000cap','Counts are live observations, not proof indexes contain all items; reconcile unique IDs with sitemap inventory','Any leaf at cap needs a verified additional filter or alternate source before completeness can be claimed']}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();e=unwrap(json.loads(a.evidence.read_text()));tree=e.get('tree',e)['response'];print(json.dumps(plan(tree),indent=2))
