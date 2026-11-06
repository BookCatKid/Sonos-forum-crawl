"""Offline integrity/coverage audit over immutable receipts, never body keyword analysis."""
import argparse,collections,gzip,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
from api_adapter import APIStore

def audit(root):
 root=Path(root);store=APIStore(root);groups=collections.defaultdict(dict);issues=[];hosts=collections.Counter();routes=collections.Counter();n=0
 for row in store.db.execute('SELECT * FROM receipts ORDER BY rowid'):
  p=root/row['path']
  try:e=json.loads(gzip.decompress(p.read_bytes()))
  except (OSError,ValueError,EOFError) as error:
   issues.append({'kind':'unreadable-receipt','id':row['id'],'error':type(error).__name__});continue
  n+=1
  if hashlib.sha256(json.dumps(e,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=row['id']:issues.append({'kind':'receipt-hash','id':row['id']})
  if not isinstance(e,dict) or not isinstance(e.get('url'),str) or not isinstance(e.get('status'),int):
   issues.append({'kind':'malformed-receipt-envelope','id':row['id']});continue
  host=urlsplit(e['url']).hostname;route=urlsplit(e['url']).path;hosts[host]+=1;routes[route]+=1
  if route!='/contentNavigation/topics' or e['status']!=200:continue
  try:
   request=e['request'];page=request['pagination']['page'];identity=json.loads(json.dumps(request));identity['pagination'].pop('page');key=(host,json.dumps(identity,sort_keys=True));topics=e['response']['topics'];ids=[str(t['publicId']) for t in topics];total=e['response']['total'];size=request['pagination']['pageSize']
   if not isinstance(total,int) or total<0 or not isinstance(size,int) or size<1 or not isinstance(page,int) or page<1:raise ValueError('invalid pagination')
  except (KeyError,TypeError,ValueError):
   issues.append({'kind':'malformed-listing-receipt','id':row['id']});continue
  if len(ids)!=len(set(ids)):issues.append({'kind':'duplicate-on-page','host':host,'page':page})
  groups[key][page]={'ids':ids,'total':total,'size':size}
 families=[]
 for (host,key),pages in groups.items():
  ids=set();overlaps=[];totals={p['total'] for p in pages.values()}
  for page,p in sorted(pages.items()):
   overlap=ids.intersection(p['ids'])
   if overlap:overlaps.append({'page':page,'public_ids':sorted(overlap)})
   ids.update(p['ids'])
  expected=max(totals);size=next(iter(pages.values()))['size'];last=(min(expected,10000)+size-1)//size;missing=[p for p in range(1,last+1) if p not in pages]
  families.append({'host':host,'request_family':json.loads(key),'pages_captured':len(pages),'unique_ids':len(ids),'reported_totals':sorted(totals),'overlaps':overlaps,'missing_pages_count':len(missing),'first_missing_pages':missing[:5],'cap_exceeded':expected>10000,'count_consistent':not missing and len(ids)==expected and len(totals)==1 and not overlaps})
 expected_projection={}
 for row in store.db.execute('SELECT host,public_id,raw_json FROM topic_observations ORDER BY rowid'):expected_projection[(row[0],row[1])]=row[2]
 actual_projection={(row[0],row[1]):row[2] for row in store.db.execute('SELECT host,public_id,raw_json FROM topic_rows')}
 projection={'missing':len(expected_projection.keys()-actual_projection.keys()),'extra':len(actual_projection.keys()-expected_projection.keys()),'mismatched':sum(actual_projection[k]!=v for k,v in expected_projection.items() if k in actual_projection)}
 if any(projection.values()):issues.append({'kind':'topic-projection-discrepancy',**projection})
 malformed=store.db.execute("SELECT count(*) FROM topic_rows WHERE json_extract(raw_json,'$.contentType') IS NULL OR json_type(raw_json,'$.content')!='text'").fetchone()[0]
 return {'receipts_hash_checked':n,'topic_projection_audit':projection,'issues':issues,'malformed_current_topics':malformed,'requests_by_host':dict(hosts),'requests_by_route':dict(routes),'listing_families':families,'count_consistent_listing_families':sum(f['count_consistent'] for f in families),'all_normal_listing_families_consistent':bool(families) and all(f['count_consistent'] for f in families),'complete':False}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();print(json.dumps(audit(a.root),indent=2))
