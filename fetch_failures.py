"""Persistent fetch failure data. No retries, network or ID-specific rules."""
import json
from pathlib import Path
from urllib.parse import urlsplit

def ensure(db):
 db.execute('CREATE TABLE IF NOT EXISTS fetch_failures(url TEXT,host TEXT,category_id INTEGER,reason TEXT,at TEXT,stage TEXT)')

def blocked(store,row):
 ensure(store.db);host=urlsplit(row['url']).hostname;q=json.loads(row['request']);categories=q.get('filters',{}).get('category',[])+([q['categoryId']] if 'categoryId' in q else [])
 failures=list(store.db.execute('SELECT url,category_id FROM fetch_failures WHERE host=?',(host,)))
 if not failures:return False
 topic=urlsplit(row['url']).path.split('/')
 if len(topic)>2 and topic[1]=='topic':
  t=store.db.execute('SELECT raw_json FROM topic_rows WHERE host=? AND public_id=?',(host,topic[2])).fetchone()
  if t and json.loads(t[0]).get('categoryId') is not None:categories.append(json.loads(t[0])['categoryId'])
 return any((r[1] is None and r[0]==row['url']) or r[1] in categories for r in failures)

def record(store,row,reason,at):
 ensure(store.db);q=json.loads(row['request']);cats=q.get('filters',{}).get('category',[])+([q['categoryId']] if 'categoryId' in q else [])
 with store.db:
  for c in cats or [None]:store.db.execute('INSERT INTO fetch_failures VALUES(?,?,?,?,?,?)',(row['url'],urlsplit(row['url']).hostname,c,reason,at,'task'))
  store.db.execute("UPDATE tasks SET state='failed' WHERE url=? AND state='pending'",(row['url'],))

def import_observed(root):
 """Recover old diagnostic data only when same-client public access was checked."""
 from public_store import PublicStore
 store=PublicStore(root);ensure(store.db)
 try:
  rows=json.loads((Path(__file__).parent/'observed-fetch-failures.json').read_text())
  with store.db:
   for r in rows:
    h=urlsplit(r['url']).hostname
    if store.db.execute('SELECT 1 FROM fetch_failures WHERE url=? AND at=?',(r['url'],r['at'])).fetchone():continue
    stop=store.db.execute('SELECT reason,at FROM transport_stops WHERE host=?',(h,)).fetchone()
    if not stop or tuple(stop)!=(r['reason'],r['stop_at']):continue
    proof=r['public_access_check']
    if proof['status']!=200 or urlsplit(proof['url']).hostname!=h or proof['at']<=r['at']:raise ValueError('invalid public access evidence')
    store.db.execute('INSERT INTO fetch_failures VALUES(?,?,?,?,?,?)',(r['url'],h,r['category_id'],r['reason'],r['at'],'task'))
    store.db.execute('DELETE FROM transport_stops WHERE host=? AND reason=? AND at=?',(h,r['reason'],r['stop_at']))
    phase=store.db.execute("SELECT value FROM controller WHERE key='phase'").fetchone()
    if phase and phase[0]=='replies':
     from scope_guard import permitted,load_scope
     policy=load_scope(Path(root)/'scope.json')
     for topic in store.db.execute('SELECT public_id FROM topic_rows WHERE host=?',(h,)).fetchall():
      url='https://'+h+'/topic/'+topic[0]+'/replies'
      if permitted(url,policy) and not blocked(store,{'url':url,'request':'{}'}):store.plan_replies(h,topic[0])
 finally:store.db.close()
