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
 source=current_generation(remote)
 # Inspect the verified durable source before reserving another generation. A
 # finished/paused phase produces no new releases and no forum requests.
 with tempfile.TemporaryDirectory() as d:
  state=Path(d)/'state';restore(source,remote,state);check_public(state);store=PublicStore(state)
  tables={r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  stops={r[0] for r in store.db.execute('SELECT host FROM transport_stops')} if 'transport_stops' in tables else set()
  phase=store.db.execute("SELECT value FROM controller WHERE key='phase'").fetchone()[0]
  from urllib.parse import urlsplit
  pending=[r for r in phase_tasks(store,phase) if urlsplit(r['url']).hostname not in stops]
  store.db.close()
  if not pending and stops:return {'paused':True,'reason':'remaining tasks blocked by persistent host stops','stopped_hosts':sorted(stops),'source_generation':source}
  if not pending:return {'paused':True,'reason':'phase queue empty; inventory/phase review required','source_generation':source}

 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
 if not str(run_id).isdigit():raise ValueError('numeric workflow run ID required')
 generation='checkpoint-'+stamp+'-'+str(run_id)
 def bounded(root,n,**kwargs):
  from parser_migration import apply
  apply(root)
  return run_batch(root,n,max_seconds=1200,**kwargs)
 return resume(remote,source,generation,root,requests,allow_large_batch=True,run=bounded)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repository',required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--run-id',required=True);a=p.parse_args()
 if a.repository!='BookCatKid/Sonos-forum-crawl':raise ValueError('wrong repo')
 print(json.dumps(scheduled(ReleaseTransport(a.repository),a.root,a.run_id)))
