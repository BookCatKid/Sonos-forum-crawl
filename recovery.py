"""Recover only terminated hosted attempts; preserve assets and explicit gaps."""
import json,re
from generations import RemoteError

def abandoned(remote,g):
 try:raw=remote.get(g,'ABANDONED.json')
 except (FileNotFoundError,KeyError):return None
 try:
  try:remote.get(g,'COMMITTED')
  except (FileNotFoundError,KeyError):pass
  else:raise ValueError('committed generation cannot be abandoned')
  m=json.loads(raw);attempt=json.loads(remote.get(g,'ATTEMPT.json'))
  if set(m)!={'version','generation','source','run_id','reason'} or m['version']!=1 or m['generation']!=g or m['source']!=attempt['source'] or attempt!={'version':1,'source':m['source'],'generation':g} or m['reason']!='terminated hosted run; uncommitted capture gap' or not re.fullmatch(r'checkpoint-\d{8}-\d{6}-'+str(m['run_id']),g):raise ValueError()
 except Exception:raise RemoteError('invalid abandoned generation record') from None
 return m

def recover_terminated(remote):
 gaps=[];inventory=remote.generations()
 for g in inventory:
  old=abandoned(remote,g)
  if old:gaps.append(old);continue
  try:remote.get(g,'COMMITTED');continue
  except (FileNotFoundError,KeyError):pass
  try:a=json.loads(remote.get(g,'ATTEMPT.json'))
  except Exception:raise RemoteError('unrecognized incomplete generation; no automatic recovery') from None
  match=re.fullmatch(r'checkpoint-\d{8}-\d{6}-(\d+)',g)
  if not match or a!={'version':1,'source':a.get('source'),'generation':g} or a['source'] not in inventory:raise RemoteError('manual or unknown attempt requires review')
  run_id=int(match[1]);run=remote.workflow_run(run_id)
  if run.get('id')!=run_id or run.get('status')!='completed' or run.get('event') not in {'schedule','workflow_dispatch'} or run.get('path')!='.github/workflows/capture.yml':raise RemoteError('originating capture run not proven terminated')
  # Parent must still be independently restorable before abandonment.
  from packed_generation import restore
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as d:restore(a['source'],remote,Path(d)/'verified')
  m={'version':1,'generation':g,'source':a['source'],'run_id':run_id,'reason':'terminated hosted run; uncommitted capture gap'}
  raw=json.dumps(m,sort_keys=True).encode();remote.put(g,'ABANDONED.json',raw)
  if remote.get(g,'ABANDONED.json')!=raw:raise RemoteError('recovery marker verification failed')
  gaps.append(m)
 return gaps
