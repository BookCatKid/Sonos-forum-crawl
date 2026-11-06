"""Review-only bounded resume entry point. No init, schedule or remote deletion.
Requires an explicit committed generation. Never infer 'latest' or start empty.
"""
import argparse,json
from pathlib import Path
from github_release import ReleaseTransport
from packed_generation import publish,restore
from public_store import PublicStore,check_public
from controller import run_batch
from run_api import Stop
from automated_batch import BackupRequired
from attempt_guard import reserve

def stop_code(error):
 s=str(error)
 if s in {'HTTP 401','HTTP 403','HTTP 404','HTTP 429','HTTP 500','HTTP 502','HTTP 503'}:return s
 if 'challenge' in s:return 'challenge'
 if 'robots' in s:return 'robots'
 if isinstance(error,TimeoutError):return 'timeout'
 if isinstance(error,OSError):return 'network'
 return 'unexpected-response'

def resume_once(remote,source,generation,root,requests=15,run=run_batch):
 if requests<1 or requests>15:raise ValueError('review-only cap1..15')
 root=Path(root)
 if root.exists():raise ValueError('resume target must not exist')
 restore(source,remote,root);check_public(root)
 if (root/'BACKUP_FAILED').exists():raise BackupRequired('prior checkpoint contains backup-failure latch')
 reserved=reserve(remote,source,generation)
 # Immutable restore is a verified durable baseline. Policy excludes are applied
 # by the controller; every ingested receipt is projected before disk storage.
 try:
  result=run(root,requests,delay=1,store_factory=PublicStore,stop_reason=stop_code)
 except Exception:
  # No details are published through this entry point. Local state remains for
  # review; never swallow a controller failure or continue another batch.
  raise
 try:publish(root,generation,reserved)
 except Exception as error:
  (root/'BACKUP_FAILED').write_text('checkpoint-failed\n')
  raise BackupRequired('remote checkpoint failed; no further collection') from error
 return {'captured_this_batch':result['captured_this_batch'],'phase':result['phase'],'reason':result['reason'],'committed_generation':generation}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repository',required=True);p.add_argument('--source-generation',required=True);p.add_argument('--generation',required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--requests',type=int,default=15);a=p.parse_args()
 print(json.dumps(resume_once(ReleaseTransport(a.repository),a.source_generation,a.generation,a.root,a.requests)))
