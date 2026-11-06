"""Fail-closed task filtering for the reviewed crawl scope. No network."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from mirror import HOSTS

def load_scope(path):
 p=json.loads(Path(path).read_text())
 if set(p['hosts'])!=HOSTS or p['minimum_delay_seconds']<1:raise ValueError('invalid host/rate policy')
 if p.get('anonymous_only') is not True or p.get('text_scanning') is not False or p.get('media_bytes') is not False:raise ValueError('unapproved scope expansion')
 if not set(('6935006','4479975','5219128','5487455','5936784','6014182')).issubset(p['excluded_en_topic_ids']):raise ValueError('missing carried exclusions')
 return p

def permitted(url,policy):
 u=urlsplit(url)
 if u.scheme!='https' or u.hostname not in policy['hosts'] or u.username or u.password or u.port not in (None,443):return False
 if url in policy['excluded_routes'] or u.path.startswith('/ideas/'):return False
 if u.hostname=='en.community.sonos.com' and u.path.startswith('/topic/'):
  parts=u.path.split('/')
  if len(parts)>2 and parts[2] in policy['excluded_en_topic_ids']:return False
 return True

def enforce(store,policy):
 store.db.execute('CREATE TABLE IF NOT EXISTS policy_exclusions(task_id TEXT PRIMARY KEY,url TEXT,reason TEXT)')
 with store.db:
  for row in store.db.execute("SELECT id,url FROM tasks WHERE state='pending'").fetchall():
   if not permitted(row['url'],policy):
    store.db.execute('INSERT OR REPLACE INTO policy_exclusions VALUES(?,?,?)',(row['id'],row['url'],'carried scope exclusion'))
    store.db.execute("UPDATE tasks SET state='failed' WHERE id=?",(row['id'],))
 return store.db.execute('SELECT count(*) FROM policy_exclusions').fetchone()[0]
