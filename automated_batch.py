"""Storage-independent unattended batch gate. No storage or launch is configured."""
import json
from pathlib import Path
from api_adapter import APIStore
from controller import run_batch
from scope_guard import load_scope,enforce
from generations import publish

class BackupRequired(Exception):pass

def one_batch(root,remote,generation,requests=15,host=None,run=run_batch):
 root=Path(root)
 if (root/'BACKUP_FAILED').exists():raise BackupRequired('persistent backup failure requires recovery review')
 policy=load_scope(root/'scope.json')  # Mandatory; no defaults when absent.
 if requests<1 or requests>15:raise ValueError('reviewed automated cap is15')
 # Verify that the prior local state has a durable baseline before any network.
 # The transport implementation and launch workflow remain separately reviewed.
 baseline=generation+'-before'
 publish(root,baseline,remote)
 store=APIStore(root);enforce(store,policy);store.db.close()
 result=run(root,requests,host_filter=host,delay=policy['minimum_delay_seconds'])
 try:publish(root,generation+'-after',remote)
 except Exception as error:
  (root/'BACKUP_FAILED').write_text('Remote checkpoint failed after '+generation+'; restore/recovery review required.\n')
  raise BackupRequired('batch saved locally, remote checkpoint failed; do not run another batch') from error
 return result
