"""Reviewed finite plan projection. Source paths become public evidence URLs."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from public_payload import public_request,PublicationError
from mirror import HOSTS

def normalize(plan,evidence_root):
 evidence_root=Path(evidence_root);sources={}
 for f in evidence_root.glob('*.json'):
  d=json.loads(f.read_text())
  if isinstance(d,dict):sources[str(f)]=d.get('source_url') or d.get('url')
 out={'schema_version':1,'categories':[],'standalone_reads':[],'coverage_complete':False}
 for c in plan['categories']:
  if c['host'] not in HOSTS:raise PublicationError('unapproved plan host')
  u=urlsplit(c['url'])
  if u.scheme!='https' or u.hostname!=c['host'] or u.query or u.fragment:raise PublicationError('unsafe plan category URL')
  row={k:c[k] for k in ('host','id','url','container','expected_count','surface','supported_types','children') if k in c}
  row['source_url']=sources.get(c['source']) or c['url']
  for key in ('first_request','additional_reads'):
   reads=([c[key]] if key=='first_request' else c.get(key,[])) if key in c else []
   for r in reads:public_request(r['url'],r.get('method','POST'),r.get('request',{}))
   if key=='first_request' and reads:row[key]=reads[0]
   if key=='additional_reads':row[key]=reads
  if not c['container']:
   row['needs_subpartition']=c.get('needs_subpartition',False);row['type_partitions']=c.get('type_partitions',[])
   for request in row['type_partitions']:public_request(row['first_request']['url'],'POST',request)
  out['categories'].append(row)
 for r in plan['standalone_reads']:
  public_request(r['url'],r['method'],r.get('request',{}));out['standalone_reads'].append(r)
 return out

def validate_pinned(plan):
 expected=json.loads((Path(__file__).parent/'approved-discovery-plan.json').read_text())
 if plan!=expected:raise PublicationError('plan differs from reviewed public plan')
 return plan
