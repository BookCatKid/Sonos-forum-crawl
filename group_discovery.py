"""Minimal public group discovery projections, not raw page/session capture."""
import re
from urllib.parse import urlsplit
from public_payload import PublicationError,fields

def group_path(path):return path=='/groups' or bool(re.fullmatch(r'/groups/[A-Za-z0-9%_-]+-\d+/?',path))

def parse_group_page(body,url):
 from inspect_surfaces import Props
 from discover_groups import group_rows
 p=Props();p.feed(body.decode(errors='replace'))
 if urlsplit(url).path=='/groups':
  return {'groups':[{k:g[k] for k in ['id','url','type']} for g in group_rows(body.decode(errors='replace'),urlsplit(url).hostname)]}
 rows=[r['props'] for r in p.rows if 'GroupTopicsList' in (r['component']or'')]
 if len(rows)!=1:raise PublicationError('unreviewed group page shape')
 d=rows[0];g=d.get('group',{})
 return {'id':d.get('categoryId'),'canSee':d.get('canSee'),'visibility':g.get('visibility'),'totalTopicsCount':d.get('totalTopicsCount'),'supportedContentTypes':d.get('supportedContentTypes',[])}

def project_group(body,url):
 host=urlsplit(url).hostname
 if urlsplit(url).path=='/groups':
  b=fields(body,['groups']);rows=[]
  for raw in b['groups']:
   g=fields(raw,['id','url','type'])
   if type(g['id'])!=int or g['id']<1 or g['type']!=4 or urlsplit(g['url']).hostname!=host or not group_path(urlsplit(g['url']).path) or not urlsplit(g['url']).path.rstrip('/').endswith('-'+str(g['id'])) or urlsplit(g['url']).query:raise PublicationError('invalid public group identity')
   rows.append(g)
  return {'groups':rows}
 b=fields(body,['id','canSee','visibility','totalTopicsCount','supportedContentTypes'])
 if type(b['id'])!=int or not urlsplit(url).path.rstrip('/').endswith('-'+str(b['id'])) or b['canSee']is not True or b['visibility']!='public' or type(b['totalTopicsCount'])!=int or b['totalTopicsCount']<0 or not b['supportedContentTypes'] or any(t not in ['article','question','conversation'] for t in b['supportedContentTypes']):raise PublicationError('group visibility/contract not public')
 return b
