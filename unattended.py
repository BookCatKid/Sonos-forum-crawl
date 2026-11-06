"""Owner-authorized ongoing cycles. Access stops are never reset here."""
import datetime,json
from urllib.parse import urlsplit
from public_store import PublicStore
from controller import phase_tasks,is_reply
from audit_api_capture import audit

def eligible_hosts(store):
 stopped={r[0] for r in store.db.execute('SELECT host FROM transport_stops')}
 from mirror import HOSTS
 return HOSTS-stopped

def cycle_action(root):
 store=PublicStore(root);hosts=eligible_hosts(store)
 phase=store.db.execute("SELECT value FROM controller WHERE key='phase'").fetchone()[0]
 pending=[r for r in phase_tasks(store,phase) if urlsplit(r['url']).hostname in hosts]
 store.db.close()
 if pending:return 'collect'
 if not hosts:return 'blocked'
 return 'replies' if phase=='listing' else 'refresh'

def prepare_action(root,action):
 store=PublicStore(root);hosts=eligible_hosts(store)
 if action=='replies':
  integrity=audit(root)
  if integrity['issues'] or integrity['malformed_current_topics']:raise ValueError('listing integrity audit failed')
  # Count drift and unavailable hosts remain explicit gaps, not completeness.
  for row in store.db.execute('SELECT host,public_id FROM topic_rows').fetchall():
   if row[0] in hosts:
    url='https://'+row[0]+'/topic/'+row[1]+'/replies'
    from scope_guard import load_scope,permitted
    if permitted(url,load_scope(store.root/'scope.json')) and not store.db.execute("SELECT 1 FROM tasks WHERE url=? AND state='failed'",(url,)).fetchone():store.plan_replies(row[0],row[1])
  with store.db:
   store.db.execute("UPDATE controller SET value='replies' WHERE key='phase'")
   store.db.execute('INSERT INTO controller VALUES(?,?)',('unattended_boundary','listing integrity checked; drift and unavailable coverage retained'))
 elif action=='refresh':
  # Every cycle refreshes all currently known eligible listings and all roots.
  # Historical observations/receipts survive; no deletion inferred from absence.
  with store.db:
   for row in store.db.execute('SELECT id,url,state FROM tasks').fetchall():
    if urlsplit(row['url']).hostname not in hosts or row['state']=='failed':continue
    store.db.execute('UPDATE tasks SET state=? WHERE id=?',('stale' if is_reply(row['url']) else 'pending',row['id']))
   store.db.execute("UPDATE controller SET value='listing' WHERE key='phase'")
   store.db.execute('INSERT INTO controller VALUES(?,?)',('unattended_boundary','new refresh cycle; prior receipt history retained'))
 elif action not in ('collect','blocked'):raise ValueError('invalid cycle action')
 store.db.close()
