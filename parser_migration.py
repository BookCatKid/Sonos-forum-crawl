"""One exact reviewed validator repair, never a generic stop reset."""
import json,sqlite3
from urllib.parse import urlsplit

FIXES={
 'de.community.sonos.com':('unexpected-response','2026-11-06T04:43:13.592637+00:00',223396),
 'en.community.sonos.com':('public-schema','2026-11-06T04:54:46.998518+00:00',229090)}
KEY='parser_fix_empty_author_id_v1'

def apply(root):
 db=sqlite3.connect(str(root)+'/api.sqlite')
 try:
  if db.execute('SELECT 1 FROM controller WHERE key=?',(KEY,)).fetchone():return []
  removed=[]
  for host,(reason,at,category) in FIXES.items():
   row=db.execute('SELECT reason,at FROM transport_stops WHERE host=?',(host,)).fetchone()
   if row!=(reason,at):continue
   tasks=db.execute("SELECT url,request FROM tasks WHERE state='pending'").fetchall()
   if not any(url=='https://'+host+'/contentNavigation/topics' and json.loads(payload).get('filters',{}).get('category')==[category] and json.loads(payload).get('pagination',{}).get('page')==1 for url,payload in tasks):raise ValueError('parser migration pending evidence mismatch')
   db.execute('DELETE FROM transport_stops WHERE host=? AND reason=? AND at=?',(host,reason,at));removed.append(host)
  if removed:db.execute('INSERT INTO controller VALUES(?,?)',(KEY,'omit absent author identity; preserve display attribution'))
  db.commit();return removed
 finally:db.close()

def apply_empty_authors(root):
 db=sqlite3.connect(str(root)+'/api.sqlite');key='parser_fix_empty_authors_v1'
 try:
  if db.execute('SELECT 1 FROM controller WHERE key=?',(key,)).fetchone():return []
  row=db.execute("SELECT reason,at FROM transport_stops WHERE host='en.community.sonos.com'").fetchone()
  if row!=('public-schema:other','2026-11-06T05:09:09.692839+00:00'):return []
  tasks=db.execute("SELECT request FROM tasks WHERE state='pending' AND url='https://en.community.sonos.com/contentNavigation/topics'").fetchall()
  if not any(json.loads(x[0]).get('filters',{}).get('category')==[229156] and json.loads(x[0]).get('pagination',{}).get('page')==1 for x in tasks):raise ValueError('empty-authors migration pending evidence mismatch')
  db.execute("DELETE FROM transport_stops WHERE host='en.community.sonos.com'");db.execute('INSERT INTO controller VALUES(?,?)',(key,'normalize empty author list to empty map'));db.commit();return ['en.community.sonos.com']
 finally:db.close()


def apply_reply_shapes(root):
 """Exact two diagnosed HTTP200 parser bugs; never clear access failures."""
 from public_store import PublicStore
 store=PublicStore(root)
 targets=[('de.community.sonos.com','public-schema:unknown-field','2026-11-06T17:43:07.640151+00:00','34077'),('nl.community.sonos.com','public-schema:other','2026-11-06T17:43:13.042725+00:00','35106')]
 changed=[]
 for host,reason,at,topic in targets:
  stop=store.db.execute('SELECT reason,at FROM transport_stops WHERE host=?',(host,)).fetchone()
  if not stop or tuple(stop)!=(reason,at):continue
  url='https://'+host+'/topic/'+topic+'/replies'
  task=store.db.execute("SELECT request FROM tasks WHERE url=? AND state='pending' ORDER BY rowid LIMIT 1",(url,)).fetchone()
  if not task or json.loads(task[0])!={'offset':0,'pageSize':20,'sort':'oldestFirst'}:raise ValueError('reply parser migration state differs')
  with store.db:
   store.db.execute('DELETE FROM transport_stops WHERE host=?',(host,))
   store.db.execute('INSERT INTO controller VALUES(?,?)',('parser_fix_reply_shapes_v1',host))
  changed.append(host)
 store.db.close();return changed
