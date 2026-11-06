"""Attach audited public configuration routes/seeds to the saved discovery plan."""
import json
from pathlib import Path

def integrate(plan,audits):
 pages={}
 for a in audits:pages.update(a['pages'])
 for c in plan['categories']:
  p=pages.get(c['url'])
  if not p:continue
  c['configuration_audited']=True;c['additional_reads']=[]
  for component in p['components']:
   route=None;payload=None
   if 'CategoryListWithTopics'in component:
    c['surface']='knowledge-base';c['additional_reads'].append({'url':'https://'+c['host']+'/categoryV2/listReadableCategoryOrderTopics','method':'POST','request':{'categoryId':c['id']}});route='listReadableCategoryContent'
   elif 'content-curation/forum/TopicList'in component:route='getReadableCuratedContent'
   if route:c['additional_reads'].append({'url':'https://'+c['host']+'/categoryV2/'+route,'method':'POST','request':{'categoryId':c['id'],'type':None,'pagination':{'page':1,'pageSize':25}}})
  c['featured_crosscheck_seeds']=p['featured_ids']
 plan['configuration_audit']={'public_leaf_pages':len(pages),'all_leaf_nodes_audited':all(c.get('configuration_audited') for c in plan['categories'] if not c['container']),'denials':sum(bool(a.get('stopped')) for a in audits),'full_inventory_traversed':False}
 return plan
if __name__=='__main__':
 p=Path('discovery-plan.json');p.write_text(json.dumps(integrate(json.loads(p.read_text()),[json.loads(Path(f).read_text()) for f in ['config-audit/audit.json','config-followup/audit.json']]),indent=2))
