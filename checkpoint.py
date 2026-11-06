"""Offline checkpoint format. No network, credentials, uploads or automatic deletion."""
import argparse,hashlib,json,os,shutil,sqlite3,tempfile
from pathlib import Path
from controller import exclusive

def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def safe_path(name):
 p=Path(name)
 if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('unsafe checkpoint path')
 return p

@exclusive
def export_checkpoint(root,destination):
 root=Path(root);destination=Path(destination)
 if destination.exists():raise ValueError('checkpoint generation must be new')
 if root.is_symlink() or not (root/'api.sqlite').is_file():raise ValueError('capture database required')
 destination.parent.mkdir(parents=True,exist_ok=True)
 temporary=Path(tempfile.mkdtemp(prefix='.checkpoint-',dir=destination.parent))
 try:
  with sqlite3.connect(root/'api.sqlite') as source,sqlite3.connect(temporary/'api.sqlite') as target:
   source.backup(target)
   if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('database integrity check failed')
  # Only data evidence and explicit policy files, never session/cookie/home caches.
  names=['api.sqlite']
  if (root/'json').exists():
   for p in sorted((root/'json').rglob('*')):
    if p.is_symlink():raise ValueError('symlink in receipt tree')
    if p.is_file():
     rel=p.relative_to(root);(temporary/rel).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,temporary/rel);names.append(str(rel))
  for name in ('scope.json','discovery-plan.json','BACKUP_FAILED'):
   p=root/name
   if p.exists():
    if p.is_symlink():raise ValueError('symlink policy')
    shutil.copyfile(p,temporary/name);names.append(name)
  from audit_api_capture import audit
  result=audit(temporary)
  if result['issues'] or result['malformed_current_topics']:raise ValueError('capture integrity failure')
  manifest={'version':1,'files':[{'path':n,'size':(temporary/n).stat().st_size,'sha256':digest(temporary/n)} for n in names]}
  (temporary/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
  os.replace(temporary,destination)
  return manifest
 finally:
  if temporary.exists():shutil.rmtree(temporary)

def restore_checkpoint(source,destination):
 source=Path(source);destination=Path(destination)
 if destination.exists():raise ValueError('restore target must not exist')
 manifest=json.loads((source/'manifest.json').read_text())
 if manifest.get('version')!=1:raise ValueError('unknown manifest version')
 files=manifest['files'];names=[x['path'] for x in files]
 if len(names)!=len(set(names)) or 'api.sqlite' not in names:raise ValueError('invalid file inventory')
 for item in files:
  rel=safe_path(item['path']);p=source/rel
  if p.is_symlink() or any(x.is_symlink() for x in p.parents if x!=source.parent):raise ValueError('symlink in checkpoint')
  if p.stat().st_size!=item['size'] or digest(p)!=item['sha256']:raise ValueError('checkpoint hash mismatch')
 destination.parent.mkdir(parents=True,exist_ok=True)
 temp=Path(tempfile.mkdtemp(prefix='.restore-',dir=destination.parent))
 try:
  for item in files:
   rel=safe_path(item['path']);(temp/rel).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/rel,temp/rel)
  db=sqlite3.connect(temp/'api.sqlite')
  try:
   if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('restored database invalid')
  finally:db.close()
  from audit_api_capture import audit
  result=audit(temp)
  if result['issues'] or result['malformed_current_topics']:raise ValueError('restored receipt audit failed')
  os.replace(temp,destination)
 finally:
  if temp.exists():shutil.rmtree(temp)
 return manifest

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['export','restore']);p.add_argument('source',type=Path);p.add_argument('destination',type=Path);a=p.parse_args()
 print(json.dumps((export_checkpoint if a.action=='export' else restore_checkpoint)(a.source,a.destination)))
