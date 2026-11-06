"""Strict committed-chain selection for scheduled resume. Never fallback.
Synthetic releases are never crawl roots. An incomplete release blocks all reads.
"""
import hashlib,json
from generations import RemoteError

def current_generation(remote):
 inventory=remote.generations();parents={};real=[]
 for g in inventory:
  try:
   marker=json.loads(remote.get(g,'COMMITTED'));index=remote.get(g,'index.json')
   if hashlib.sha256(index).hexdigest()!=marker['index_sha256']:raise RemoteError('commit mismatch')
  except Exception:raise RemoteError('incomplete generation requires recovery review') from None
  if g.startswith('checkpoint-synthetic-'):continue
  real.append(g)
  try:attempt=json.loads(remote.get(g,'ATTEMPT.json'))
  except (FileNotFoundError,KeyError):continue
  if set(attempt)!={'version','source','generation'} or attempt['version']!=1 or attempt['generation']!=g or attempt['source'] not in inventory:raise RemoteError('invalid ancestry')
  parents[g]=attempt['source']
 if not real:raise RemoteError('no real committed crawl generation')
 children={}
 for child,parent in parents.items():
  if parent in children:raise RemoteError('divergent generation branches require review')
  children[parent]=child
 roots=[g for g in real if g not in parents]
 if len(roots)!=1:raise RemoteError('multiple crawl roots require review')
 seen=set();g=roots[0]
 while g in children:
  if g in seen:raise RemoteError('generation ancestry cycle')
  seen.add(g);g=children[g]
 if seen|{g}!=set(real):raise RemoteError('disconnected generation ancestry')
 return g
