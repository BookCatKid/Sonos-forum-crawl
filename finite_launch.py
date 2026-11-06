"""One explicit manual launch. No cron, retry loop or implicit phase advance."""
import argparse,json,re
from pathlib import Path
from initialize_public import initialize
from packed_generation import publish
from public_job import resume_once
from github_release import ReleaseTransport

REPOSITORY='BookCatKid/Sonos-forum-crawl'

def first_batch(remote,generation,root,requests=15,run=None):
 if not re.fullmatch(r'checkpoint-[A-Za-z0-9][A-Za-z0-9._-]{0,60}',generation):raise ValueError('checkpoint generation prefix required')
 if not 1<=requests<=15:raise ValueError('finite15APIread cap')
 # Do not create a second baseline if any prior generation is unresolved.
 for prior in remote.generations():
  marker=json.loads(remote.get(prior,'COMMITTED'));index=remote.get(prior,'index.json')
  import hashlib
  if hashlib.sha256(index).hexdigest()!=marker['index_sha256']:raise ValueError('unresolved prior generation')
 root=Path(root)
 if root.exists():raise ValueError('fresh launch root required')
 root.mkdir(parents=True)
 baseline=generation+'-baseline'
 initialize(root/'initial')
 # Empty but fully planned state must be remotely durable before forum reads.
 publish(root/'initial',baseline,remote)
 options={} if run is None else {'run':run}
 return resume_once(remote,baseline,generation,root/'capture',requests,**options)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repository',required=True);p.add_argument('--generation',required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
 if a.repository!=REPOSITORY:raise ValueError('wrong repository')
 print(json.dumps(first_batch(ReleaseTransport(a.repository),a.generation,a.root)))
