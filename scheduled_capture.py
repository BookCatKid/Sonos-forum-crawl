"""One scheduled durable batch, then exit. No phase bypass or stop reset."""
import argparse,datetime,json
from pathlib import Path
from automation_state import current_generation
from github_release import ReleaseTransport
from public_job import resume_once
from controller import run_batch
from public_store import PublicStore,check_public
from packed_generation import restore
from controller import phase_tasks
import tempfile,sqlite3

def scheduled(remote,root,run_id,requests=500,resume=resume_once):
 from recovery import recover_terminated
 gaps=recover_terminated(remote)
 source=current_generation(remote)
 from unattended import cycle_action,prepare_action
 with tempfile.TemporaryDirectory() as d:
  state=Path(d)/'state';restore(source,remote,state);check_public(state)
  from parser_migration import apply_reply_shapes,apply_attachment_shapes
  apply_reply_shapes(state);apply_attachment_shapes(state)
  from access_migration import apply_reviewed_resource_exclusion
  apply_reviewed_resource_exclusion(state)
  action=cycle_action(state)
  if action=='blocked':return {'paused':True,'reason':'all hosts stopped; no automatic access retry','source_generation':source}

 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
 if not str(run_id).isdigit():raise ValueError('numeric workflow run ID required')
 generation='checkpoint-'+stamp+'-'+str(run_id)
 def bounded(root,n,**kwargs):
  from parser_migration import apply,apply_empty_authors
  apply(root);apply_empty_authors(root);apply_reply_shapes(root);apply_attachment_shapes(root);apply_reviewed_resource_exclusion(root)
  prepare_action(root,action)
  if gaps:
   store=PublicStore(root)
   with store.db:
    for gap in gaps:store.db.execute('INSERT INTO controller VALUES(?,?)',('unattended_recovery_gap',gap['generation']))
   store.db.close()
  return run_batch(root,n,max_seconds=1200,**kwargs)
 return resume(remote,source,generation,root,requests,allow_large_batch=True,run=bounded)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repository',required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--run-id',required=True);a=p.parse_args()
 if a.repository!='BookCatKid/Sonos-forum-crawl':raise ValueError('wrong repo')
 print(json.dumps(scheduled(ReleaseTransport(a.repository),a.root,a.run_id)))
