"""Durable pre-capture attempt reservation. Unfinished attempt blocks a new run.
The reserved release must exist remotely before any forum read. No delete/reset.
"""
import hashlib,json,re
from generations import RemoteError

class ReservedTransport:
 def __init__(self,remote,generation,attempt):self.remote=remote;self.generation=generation;self.attempt=attempt;self.claimed=False
 def exists(self,g):
  if g!=self.generation:return self.remote.exists(g)
  if self.claimed:return True
  if self.remote.get(g,'ATTEMPT.json')!=self.attempt:raise RemoteError('reservation changed')
  return False
 def create(self,g):
  if g!=self.generation or self.claimed or self.remote.get(g,'ATTEMPT.json')!=self.attempt:raise RemoteError('invalid reservation')
  self.claimed=True
 def put(self,g,k,b):return self.remote.put(g,k,b)
 def get(self,g,k):return self.remote.get(g,k)

def reserve(remote,source,generation):
 for g in (source,generation):
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}',g):raise ValueError('invalid generation')
 if not hasattr(remote,'generations'):raise RemoteError('remote attempt inventory required')
 # Only dedicated checkpoint tags belong here. Any partial or unrecognized
 # generation blocks capture; do not silently skip it and pick an older good one.
 inventory=remote.generations()
 superseded=set()
 for g in inventory:
  try:
   marker=json.loads(remote.get(g,'COMMITTED'));index=remote.get(g,'index.json')
   if hashlib.sha256(index).hexdigest()!=marker['index_sha256']:raise ValueError()
  except Exception:raise RemoteError('unresolved remote generation requires recovery review') from None
 for g in inventory:
  try:attempt=json.loads(remote.get(g,'ATTEMPT.json'))
  except (KeyError,FileNotFoundError):continue
  if set(attempt)!={'version','source','generation'} or attempt['version']!=1 or attempt['generation']!=g or attempt['source'] not in inventory:raise RemoteError('invalid generation ancestry')
  superseded.add(attempt['source'])
 if source in superseded:raise RemoteError('source generation has a newer committed descendant')
 if source not in inventory:raise RemoteError('source generation absent from remote inventory')
 if generation in inventory or remote.exists(generation):raise RemoteError('attempt already exists')
 attempt=json.dumps({'version':1,'source':source,'generation':generation},sort_keys=True).encode()
 remote.create(generation);remote.put(generation,'ATTEMPT.json',attempt)
 if remote.get(generation,'ATTEMPT.json')!=attempt:raise RemoteError('attempt reservation verification failed')
 return ReservedTransport(remote,generation,attempt)
