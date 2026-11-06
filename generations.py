"""Immutable remote generation protocol. Transport supplied explicitly; no network here."""
import hashlib,json,re,tempfile
from pathlib import Path
from checkpoint import export_checkpoint,restore_checkpoint,digest

class RemoteError(Exception):pass

def publish(root,generation,remote):
 if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}',generation):raise ValueError('invalid generation')
 if remote.exists(generation):raise RemoteError('generation already exists; never overwrite')
 with tempfile.TemporaryDirectory() as d:
  snap=Path(d)/'snapshot';m=export_checkpoint(root,snap)
  remote.create(generation)
  for item in m['files']:
   data=(snap/item['path']).read_bytes()
   if len(data)>=2*1024**3:raise RemoteError('asset requires chunking before upload')
   remote.put(generation,item['path'],data)
   saved=remote.get(generation,item['path'])
   if len(saved)!=item['size'] or hashlib.sha256(saved).hexdigest()!=item['sha256']:raise RemoteError('remote asset verification failed')
  manifest=(snap/'manifest.json').read_bytes();remote.put(generation,'manifest.json',manifest)
  if remote.get(generation,'manifest.json')!=manifest:raise RemoteError('remote manifest verification failed')
  # Completion marker is the commit point; partial generations never qualify.
  marker=json.dumps({'manifest_sha256':hashlib.sha256(manifest).hexdigest()}).encode()
  remote.put(generation,'COMMITTED',marker)
  if remote.get(generation,'COMMITTED')!=marker:raise RemoteError('remote commit verification failed')
 return generation

def restore(generation,remote,destination):
 # Any missing asset/read/auth error propagates. Never start an empty crawl.
 marker=json.loads(remote.get(generation,'COMMITTED'));raw=remote.get(generation,'manifest.json')
 if hashlib.sha256(raw).hexdigest()!=marker['manifest_sha256']:raise RemoteError('manifest commitment mismatch')
 m=json.loads(raw)
 from checkpoint import safe_path
 with tempfile.TemporaryDirectory() as d:
  snap=Path(d)/'snapshot';snap.mkdir();(snap/'manifest.json').write_bytes(raw)
  for item in m['files']:
   rel=safe_path(item['path']);p=snap/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(remote.get(generation,item['path']))
  return restore_checkpoint(snap,destination)
