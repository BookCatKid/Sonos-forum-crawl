"""One reviewed public-scope change. No network, no generic stop reset."""
import json
from pathlib import Path
from public_store import PublicStore
from scope_guard import enforce
HOST='en.community.sonos.com'
KEY='reviewed_category229122_exclusion_v1'
STOP=('HTTP 403','2026-11-06T14:17:56+00:00')
def apply_reviewed_resource_exclusion(root):
 store=PublicStore(root)
 try:
  # Always carry the permanent resource/category exclusions into old snapshots.
  path=Path(root)/'scope.json';p=json.loads(path.read_text());route='https://'+HOST+'/exclusive-229122'
  if route not in p['excluded_routes']:p['excluded_routes'].append(route)
  p['excluded_en_category_ids']=sorted(set(p.get('excluded_en_category_ids',[]))|{229122});path.write_text(json.dumps(p,indent=2)+'\n');enforce(store,p)
  if store.db.execute('SELECT 1 FROM controller WHERE key=?',(KEY,)).fetchone():return False
  stop=store.db.execute('SELECT reason,at FROM transport_stops WHERE host=?',(HOST,)).fetchone()
  if not stop or tuple(stop)!=STOP:return False
  # Reviewed 2026-11-06: ordinary anonymous existing category228987 returned
  # HTTP200/6 topics. No denied URL retry, credentials or client rotation.
  with store.db:
   store.db.execute('INSERT INTO controller VALUES(?,?)',('historical_access_stop',json.dumps({'host':HOST,'reason':STOP[0],'at':STOP[1],'excluded_category':229122})))
   store.db.execute('INSERT INTO controller VALUES(?,?)',(KEY,'ordinary anonymous category228987 HTTP200; permanently exclude denied category; preserve subsequent stops'))
   store.db.execute('DELETE FROM transport_stops WHERE host=? AND reason=? AND at=?',(HOST,*STOP))
  return True
 finally:store.db.close()
