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
