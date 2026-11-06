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
from public_payload import PublicationError

def stop_code(error):
 s=str(error)
 if isinstance(error,PublicationError):
  # Only enumerated diagnostic labels/known field names, never response values.
  fixed={'nonempty attachment metadata schema awaits review':'attachments','invalid public content URL':'content-url','invalid public URL type':'url-type','unreviewed topic type':'topic-type','listing contains carried excluded identity; review boundary before storage':'excluded-id'}
  if s in fixed:return 'public-schema:'+fixed[s]
  known={'content','title','categoryName','publishedAt','lastActivityAt','replyCount','inclusiveVisibleReplyCount','hasBestAnswer','name','username','repliedAt','inclusiveReplyCount','isBestAnswer','isPinnedReply','startAt','endAt','location','timezoneName','type'}
  if s.startswith('invalid field type: ') and s.split(': ',1)[1] in known:return 'public-schema:field-type:'+s.split(': ',1)[1]
  if s.startswith('unreviewed fields:'):return 'public-schema:unknown-field'
  return 'public-schema:other'
 if s in {'HTTP 401','HTTP 403','HTTP 404','HTTP 429','HTTP 500','HTTP 502','HTTP 503'}:return s
 if 'challenge' in s:return 'challenge'
 if 'robots' in s:return 'robots'
 if isinstance(error,TimeoutError):return 'timeout'
 if isinstance(error,OSError):return 'network'
 return 'unexpected-response'

def resume_once(remote,source,generation,root,requests=15,run=run_batch,allow_large_batch=False):
 cap=500 if allow_large_batch else 15
 if requests<1 or requests>cap:raise ValueError('bounded API request cap exceeded')
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
 return {'captured_this_batch':result['captured_this_batch'],'phase':result['phase'],'reason':result['reason'],'committed_generation':generation,'transport_stops':result.get('status',{}).get('transport_stops',[])}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repository',required=True);p.add_argument('--source-generation',required=True);p.add_argument('--generation',required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--requests',type=int,default=15);a=p.parse_args()
 print(json.dumps(resume_once(ReleaseTransport(a.repository),a.source_generation,a.generation,a.root,a.requests)))
