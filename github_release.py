"""Immutable single-repo release transport. Token comes only from GITHUB_TOKEN.
No workflow is enabled by this module. No delete/update operation exists.
"""
import json,os,re,urllib.request,urllib.error
from urllib.parse import quote,urlsplit
from generations import RemoteError

class ReleaseTransport:
 def __init__(self,repository,token=None,request=None):
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',repository):raise ValueError('invalid repository')
  self.repo=repository;self.token=token if token is not None else os.environ.get('GITHUB_TOKEN')
  if not self.token:raise RemoteError('GITHUB_TOKEN required')
  self.request=request or self._request;self.releases={}
 def _request(self,method,url,data=None,ctype='application/json'):
  p=urlsplit(url)
  if p.scheme!='https' or p.hostname not in {'api.github.com','uploads.github.com'}:raise RemoteError('untrusted GitHub API origin')
  # Never pass authentication across redirects. Asset API supports a redirected
  # anonymous download URL, fetched separately without the token.
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*a,**k):return None
  headers={'Authorization':'Bearer '+self.token,'Accept':'application/octet-stream' if ctype=='download' else 'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'sonos-forum-checkpoint/1'}
  if data is not None:headers['Content-Type']=ctype
  opener=urllib.request.build_opener(NoRedirect())
  try:
   with opener.open(urllib.request.Request(url,data=data,headers=headers,method=method),timeout=60) as r:return r.read()
  except urllib.error.HTTPError as e:
   if ctype=='download' and e.code==302:
    target=e.headers.get('Location','');q=urlsplit(target)
    if q.scheme!='https' or q.hostname!='release-assets.githubusercontent.com' or q.username or q.password:raise RemoteError('untrusted asset redirect')
    with opener.open(urllib.request.Request(target,headers={'User-Agent':'sonos-forum-checkpoint/1'}),timeout=120) as r:return r.read()
   if e.code==404:raise FileNotFoundError('GitHub resource not found') from None
   raise RemoteError('GitHub request failed HTTP '+str(e.code)) from None
 def _api(self,path):return 'https://api.github.com/repos/'+self.repo+path
 def _release(self,g):
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}',g):raise ValueError('invalid generation')
  if g not in self.releases:self.releases[g]=json.loads(self.request('GET',self._api('/releases/tags/'+quote(g,safe=''))))
  return self.releases[g]
 def exists(self,g):
  try:self._release(g);return True
  except FileNotFoundError:return False
 def create(self,g):
  if self.exists(g):raise RemoteError('generation already exists')
  result=self.request('POST',self._api('/releases'),json.dumps({'tag_name':g,'name':g,'body':'Public anonymous forum checkpoint. Commit marker uploaded last.','draft':False,'prerelease':True,'make_latest':'false'}).encode())
  self.releases[g]=json.loads(result)
 def _assets(self,g):
  rid=self._release(g)['id'];out=[]
  for page in range(1,12):
   rows=json.loads(self.request('GET',self._api('/releases/'+str(rid)+'/assets?per_page=100&page='+str(page))))
   out.extend(rows)
   if len(rows)<100:return out
  raise RemoteError('asset inventory exceeds reviewed limit')
 def generations(self):
  out=[]
  for page in range(1,101):
   rows=json.loads(self.request('GET',self._api('/releases?per_page=100&page='+str(page))))
   for row in rows:
    tag=row['tag_name']
    if not tag.startswith('checkpoint-'):raise RemoteError('unreviewed release namespace')
    self.releases[tag]=row;out.append(tag)
   if len(rows)<100:return out
  raise RemoteError('release inventory exceeds reviewed bound')
 def put(self,g,name,data):
  if not re.fullmatch(r'[A-Za-z0-9._-]{1,120}',name):raise ValueError('invalid asset name')
  if len(data)>=2*1024**3:raise RemoteError('asset exceeds size bound')
  if any(x['name']==name for x in self._assets(g)):raise RemoteError('immutable asset exists')
  rid=self._release(g)['id'];url='https://uploads.github.com/repos/'+self.repo+'/releases/'+str(rid)+'/assets?name='+quote(name,safe='')
  self.request('POST',url,data,'application/octet-stream')
 def get(self,g,name):
  matches=[a for a in self._assets(g) if a['name']==name]
  if not matches:raise FileNotFoundError('asset missing')
  if len(matches)!=1:raise RemoteError('duplicate asset')
  return self.request('GET',self._api('/releases/assets/'+str(matches[0]['id'])),None,'download')
