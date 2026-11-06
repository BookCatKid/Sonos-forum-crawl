"""Build reusable public discovery plan from saved site component/category evidence."""
import argparse,json,math,re
from pathlib import Path
from urllib.parse import urlsplit
from mirror import Page,HOSTS
from api_adapter import validate

def build(files):
 categories={};sources=[];featured={}
 def walk(value,source):
  if isinstance(value,dict):
   url=value.get('url');identity=value.get('id');count=value.get('visibleTopicsCount',value.get('totalTopics'));children=value.get('children')
   if isinstance(url,str) and urlsplit(url).hostname in HOSTS and identity is not None and ('isContainer'in value or count is not None):
    host=urlsplit(url).hostname;categories[(host,str(identity))]={'host':host,'id':int(identity),'url':url,'container':bool(value.get('isContainer')),'expected_count':count,'source':source,'surface':'group' if '/groups/' in url else 'category','supported_types':value.get('supportedContentTypes',[]),'children':[str(c['id']) for c in children or [] if 'id'in c]}
   for v in value.values():
    if isinstance(v,(dict,list)):walk(v,source)
  elif isinstance(value,list):
   for v in value:walk(v,source)
 for f in files:
  p=Path(f);sources.append(str(p))
  if p.suffix=='.html' or p.suffix=='.txt':
   page=Page();page.feed(p.read_text(errors='replace'))
   for name,data in page.components:
    if 'featured-topics' in name:
     for t in data.get('featuredTopics',[]):
      if isinstance(t.get('url'),str) and urlsplit(t['url']).hostname in HOSTS:featured[(urlsplit(t['url']).hostname,str(t.get('id')))]=dict(public_id=str(t.get('id')),url=t['url'],content_type=t.get('contentType'),source=str(p))
    if name=='mega-menu/index' or 'CategoryList'in name:walk(data,str(p))
  else:
   data=json.loads(p.read_text());data=json.loads(data['content']) if 'content'in data else data;walk(data,str(p))
 rows=[]
 for c in categories.values():
  if c['container']:
   children=[categories.get((c['host'],i)) for i in c['children']];c['child_expected_sum']=sum(x['expected_count'] or 0 for x in children if x);c['count_delta']=c['expected_count']-c['child_expected_sum'] if c['expected_count'] is not None else None;c['strategy']='descend-evidenced-children';c['missing_child_metadata']=[i for i in c['children'] if (c['host'],i) not in categories]
  else:
   c['strategy']='public-category-list';c['contract_status']='working-on-host' if c['host'] in HOSTS else 'candidate';c['needs_subpartition']=c['expected_count'] is None or c['expected_count']>=10000
   payload={'sort':{'sticky':'desc','lastActivityAt':'desc'},'filters':{'contentType':[],'productArea':[],'category':[c['id']]},'pagination':{'page':1,'pageSize':25},'stripHtmlFromContent':False,'trimContentLength':0,'enableLanguageBasedCategoryFilter':False};validate('https://'+c['host']+'/contentNavigation/topics','POST',payload);c['type_partitions']=[dict(payload,filters=dict(payload['filters'],contentType=[typ])) for typ in c['supported_types']] if c['needs_subpartition'] else []
   c['first_request']={'url':'https://'+c['host']+'/contentNavigation/topics','method':'POST','request':payload};c['estimated_pages']=math.ceil(c['expected_count']/25) if c['expected_count'] is not None else None
  rows.append(c)
 return {'schema_version':1,'categories':sorted(rows,key=lambda c:(c['host'],c['id'])),'standalone_reads':[{'url':'https://'+host+route,'method':'GET','request':{}} for host in sorted(HOSTS) for route in ['/productUpdates/topics?page=1','/events/events?page=1']],'sources':sources,'featured_crosscheck_seeds':list(featured.values()),'tasks_started':0,'complete':False,'coverage_requirements':['Compare captured unique publicIds to sitemap seeds perhost','Keep API-only topics; sitemap is not exhaustive','Deduplicate category overlaps; follow response metadata rather than guessed pagination','Any leaf >=10000 needs independently verified contentType partitions with totals belowcap; type totals can drift and require ID reconciliation','Queue replies from captured topic ID/type and walk all nested parent continuations','Ideas denied route excluded; public group topic listings use evidenced category IDs; featured references are additional ID seeds','A multi-day crawl is not a simultaneous snapshot, retain capture timestamps and changed-response flags']}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('sources',nargs='+');a=p.parse_args();print(json.dumps(build(a.sources),indent=2))
