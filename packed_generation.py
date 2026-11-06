"""Chunked checkpoint release format; safe extraction, verified immutable commit.
Publication preflight is mandatory and not replaceable from the command line.
"""
import hashlib,io,json,re,tarfile,tempfile,sqlite3,os
from pathlib import Path
from checkpoint import export_checkpoint,restore_checkpoint,safe_path,digest
from public_store import check_public
from generations import RemoteError

CHUNK=128*1024*1024

def publish(root,generation,remote,chunk_size=CHUNK):
 if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}',generation):raise ValueError('invalid generation')
 if not 1024<=chunk_size<=CHUNK:raise ValueError('chunk size outside bound')
 if remote.exists(generation):raise RemoteError('immutable generation exists')
 with tempfile.TemporaryDirectory() as d:
  d=Path(d);snap=d/'snapshot';export_checkpoint(root,snap);preflight=check_public(snap)
  # Rebuild the approved logical rows in a fresh database to avoid releasing
  # deleted/free-page remnants, unknown SQL constraints or user_version bytes.
  from api_adapter import APIStore
  from public_state import EXTRA
  original=sqlite3.connect(snap/'api.sqlite');fresh=APIStore(d/'fresh')
  for name,columns in EXTRA.items():
   if original.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone():
    fresh.db.execute('CREATE TABLE '+name+'('+','.join(k+' '+t for k,t in columns)+')')
  for (name,) in original.execute("SELECT name FROM sqlite_master WHERE type='table'"):
   rows=original.execute('SELECT * FROM '+name)
   for row in rows:fresh.db.execute('INSERT INTO '+name+' VALUES('+','.join('?' for _ in row)+')',row)
  fresh.db.commit();fresh.db.close();original.close();os.replace(d/'fresh/api.sqlite',snap/'api.sqlite')
  manifest=json.loads((snap/'manifest.json').read_text())
  for item in manifest['files']:
   if item['path']=='api.sqlite':item.update(size=(snap/'api.sqlite').stat().st_size,sha256=digest(snap/'api.sqlite'))
  (snap/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2));check_public(snap)
  archive=d/'checkpoint.tar.gz'
  with tarfile.open(archive,'w:gz') as tar:
   for p in sorted(snap.rglob('*')):
    if p.is_file():tar.add(p,arcname=str(p.relative_to(snap)),recursive=False)
  count=(archive.stat().st_size+chunk_size-1)//chunk_size
  if count>990:raise RemoteError('checkpoint exceeds bounded release assets')
  remote.create(generation);parts=[]
  with archive.open('rb') as f:
   for i in range(count):
    data=f.read(chunk_size);name='checkpoint.part-%04d'%i;sha=hashlib.sha256(data).hexdigest()
    remote.put(generation,name,data)
    if hashlib.sha256(remote.get(generation,name)).hexdigest()!=sha:raise RemoteError('remote chunk mismatch')
    parts.append({'name':name,'size':len(data),'sha256':sha})
  index=json.dumps({'version':1,'parts':parts,'archive_size':archive.stat().st_size,'archive_sha256':digest(archive),'public_schema_version':preflight['public_schema_version']},sort_keys=True).encode()
  remote.put(generation,'index.json',index)
  if remote.get(generation,'index.json')!=index:raise RemoteError('remote index mismatch')
  # Download/restore audit before marker. It checks the complete remote archive,
  # not only each upload response. No current pointer is replaced.
  _restore(generation,remote,d/'verified',index)
  marker=json.dumps({'index_sha256':hashlib.sha256(index).hexdigest()},sort_keys=True).encode();remote.put(generation,'COMMITTED',marker)
  if remote.get(generation,'COMMITTED')!=marker:raise RemoteError('remote commit mismatch')
 return generation

def _restore(generation,remote,destination,index):
 m=json.loads(index)
 if m.get('version')!=1 or m.get('public_schema_version')!=1:raise RemoteError('unknown generation format')
 parts=m['parts']
 if not 1<=len(parts)<=990 or len({p['name'] for p in parts})!=len(parts):raise RemoteError('invalid parts')
 with tempfile.TemporaryDirectory() as d:
  d=Path(d);archive=d/'checkpoint.tar.gz'
  with archive.open('wb') as f:
   for i,p in enumerate(parts):
    if p['name']!='checkpoint.part-%04d'%i or not 0<p['size']<=CHUNK:raise RemoteError('invalid part')
    data=remote.get(generation,p['name'])
    if len(data)!=p['size'] or hashlib.sha256(data).hexdigest()!=p['sha256']:raise RemoteError('part hash mismatch')
    f.write(data)
  if archive.stat().st_size!=m['archive_size'] or digest(archive)!=m['archive_sha256']:raise RemoteError('archive mismatch')
  snap=d/'snapshot';snap.mkdir();names=set();size=0
  with tarfile.open(archive,'r:gz') as tar:
   for member in tar:
    rel=safe_path(member.name)
    if not member.isfile() or member.name in names:raise RemoteError('nonregular or duplicate archive member')
    size+=member.size
    if size>8_000_000_000:raise RemoteError('archive expands beyond reviewed disk budget')
    names.add(member.name);target=snap/rel;target.parent.mkdir(parents=True,exist_ok=True)
    with tar.extractfile(member) as src,target.open('wb') as out:
     import shutil
     shutil.copyfileobj(src,out)
  manifest=json.loads((snap/'manifest.json').read_text())
  if names!={i['path'] for i in manifest['files']}|{'manifest.json'}:raise RemoteError('unexpected archive file')
  # Verify before target creation. A failed restore never initializes empty.
  restore_checkpoint(snap,d/'checked');check_public(d/'checked')
  return restore_checkpoint(snap,destination)

def restore(generation,remote,destination):
 marker=json.loads(remote.get(generation,'COMMITTED'));index=remote.get(generation,'index.json')
 if hashlib.sha256(index).hexdigest()!=marker['index_sha256']:raise RemoteError('uncommitted index')
 return _restore(generation,remote,destination,index)
