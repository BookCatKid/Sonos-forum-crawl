"""Compare captured API topic identities with saved sitemap URL inventory, offline."""
import argparse,json,re
from pathlib import Path
from urllib.parse import urlsplit
from api_adapter import APIStore

def reconcile(store,paths):
 indexed={};unparsed=[]
 for path in paths:
  for u in Path(path).read_text().splitlines():
   m=re.search(r'-(\d+)/?$',urlsplit(u).path)
   if m:indexed[(urlsplit(u).hostname,m[1])]=u
   elif u.strip():unparsed.append(u)
 captured={(r[0],r[1]) for r in store.db.execute('SELECT host,public_id FROM topic_rows')}
 result=[]
 for host in sorted({h for h,_ in indexed}|{h for h,_ in captured}):
  i={x for x in indexed if x[0]==host};c={x for x in captured if x[0]==host};result.append({'host':host,'indexed_topics':len(i),'captured_api_topics':len(c),'captured_also_indexed':len(i&c),'indexed_not_yet_captured':len(i-c),'api_not_in_saved_index':len(c-i),'api_not_in_index_examples':[x[1] for x in sorted(c-i)[:10]]})
 return {'by_host':result,'unparsed_index_urls':unparsed,'complete':False,'interpretation':'Set comparison of saved snapshots only. Missing means not captured in bounded prototype, not unavailable. API-only IDs may be new/unindexed or index drift; no global completeness claim.'}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--inventory',type=Path,nargs='+',required=True);a=p.parse_args();print(json.dumps(reconcile(APIStore(a.root),a.inventory),indent=2))
