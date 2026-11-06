"""Exact public checkpoint database/state contract. No body classification."""
import datetime,hashlib,json,re,sqlite3,tempfile
from pathlib import Path
from api_adapter import APIStore
from mirror import HOSTS
from public_payload import PublicationError

EXTRA={
 'event_pages':[('url','TEXT'),('raw_json','TEXT')],
 'controller':[('key','TEXT'),('value','TEXT')],
 'transport_stops':[('host','TEXT'),('reason','TEXT'),('at','TEXT')],
 'resource_stops':[('task_id','TEXT'),('url','TEXT'),('reason','TEXT'),('at','TEXT')],
 'policy_exclusions':[('task_id','TEXT'),('url','TEXT'),('reason','TEXT')]}

def timestamp(value):
 try:
  d=datetime.datetime.fromisoformat(value)
  if d.tzinfo is None:raise ValueError()
 except (ValueError,TypeError):raise PublicationError('invalid metadata timestamp')

def validate_state(db,root):
 with tempfile.TemporaryDirectory() as d:
  baseline=APIStore(d);expected={r[0]:[(c[1],c[2]) for c in baseline.db.execute('PRAGMA table_info('+r[0]+')')] for r in baseline.db.execute("SELECT name FROM sqlite_master WHERE type='table'")};baseline.db.close()
 expected.update(EXTRA)
 for name,kind,sql in db.execute("SELECT name,type,sql FROM sqlite_master WHERE type='index'"):
  if sql is not None or not name.startswith('sqlite_autoindex_'):raise PublicationError('unreviewed database index')
 for name,kind in db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger')"):
  if kind!='table' or name not in expected:raise PublicationError('unreviewed database object')
  if [(r[1],r[2]) for r in db.execute('PRAGMA table_info('+name+')')]!=expected[name]:raise PublicationError('unreviewed table columns')
 tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 if (Path(root)/'BACKUP_FAILED').exists() and (Path(root)/'BACKUP_FAILED').read_bytes()!=b'checkpoint-failed\n':raise PublicationError('unreviewed failure-latch metadata')
 canonical=json.loads((Path(__file__).parent/'scope.json').read_text());actual=json.loads((Path(root)/'scope.json').read_text())
 if actual!=canonical:raise PublicationError('public scope metadata differs from reviewed policy')
 tasks={r['id']:r for r in db.execute('SELECT * FROM tasks')}
 for row in tasks.values():
  if row['id']!=hashlib.sha256((row['url']+json.dumps(json.loads(row['request']),sort_keys=True)).encode()).hexdigest():raise PublicationError('task identity mismatch')
  if row['kind'] not in {'observed','phase-listing','standalone-listing','knowledge-base-continuation','product-update-continuation','event-continuation','topic-list-continuation','nested-continuation','reply-continuation','topic-replies'} or row['state'] not in {'pending','captured','failed','stale'}:raise PublicationError('unreviewed task metadata')
 for table in ('topic_rows','topic_observations','reply_rows','reply_observations','reply_expectations','known_topic_types','totals'):
  for row in db.execute('SELECT * FROM '+table):
   if row['host'] not in HOSTS:raise PublicationError('unknown host')
   for col in row.keys():
    if col in {'public_id','topic_public_id','internal_id','parent_internal_id'} and row[col] not in (None,'') and not re.fullmatch(r'\d+',str(row[col])):raise PublicationError('invalid content identity')
   if 'expected' in row.keys() and (type(row['expected'])!=int or row['expected']<0):raise PublicationError('invalid expected count')
   if 'content_type' in row.keys() and row['content_type'] not in {'question','conversation','article','idea','productUpdate'}:raise PublicationError('unknown content type')
 if 'controller' in tables:
  for row in db.execute('SELECT * FROM controller'):
   if row['key'] in {'prepared','last_checkpoint'}:timestamp(row['value'])
   elif row['key']=='parser_fix_empty_author_id_v1':
    if row['value']!='omit absent author identity; preserve display attribution':raise PublicationError('unreviewed parser migration')
   elif row['key']=='parser_fix_reply_shapes_v1':
    if row['value'] not in {'de.community.sonos.com','nl.community.sonos.com'}:raise PublicationError('invalid reply parser migration')
   elif row['key']=='parser_fix_empty_authors_v1':
    if row['value']!='normalize empty author list to empty map':raise PublicationError('unreviewed parser migration')
   elif row['key']=='unattended_recovery_gap':
    if not re.fullmatch(r'checkpoint-\d{8}-\d{6}-\d+',row['value']):raise PublicationError('invalid recovery gap generation')
   elif row['key']=='unattended_boundary':
    if row['value'] not in {'listing integrity checked; drift and unavailable coverage retained','new refresh cycle; prior receipt history retained'}:raise PublicationError('unreviewed unattended boundary')
   elif row['key']=='phase':
    if row['value'] not in {'listing','replies'}:raise PublicationError('unknown phase')
   else:raise PublicationError('unreviewed controller metadata')
 # Arbitrary exception strings can contain server text or runtime details. A new
 # public orchestration path must store reviewed codes, not arbitrary str(error).
 if 'transport_stops' in tables:
  for row in db.execute('SELECT * FROM transport_stops'):
   from public_job import stop_code
   allowed={'HTTP 401','HTTP 403','HTTP 404','HTTP 429','HTTP 500','HTTP 502','HTTP 503','timeout','network','challenge','robots','unexpected-response','public-schema'}
   allowed.update(stop_code(PublicationError(v)) for v in ['nonempty attachment metadata schema awaits review','invalid public content URL','invalid public URL type','unreviewed topic type','listing contains carried excluded identity; review boundary before storage','unreviewed fields:fixture','other'])
   allowed.update('public-schema:field-type:'+v for v in ['content','title','categoryName','publishedAt','lastActivityAt','replyCount','inclusiveVisibleReplyCount','hasBestAnswer','name','username','repliedAt','inclusiveReplyCount','isBestAnswer','isPinnedReply','startAt','endAt','location','timezoneName','type'])
   if row['host'] not in HOSTS or row['reason'] not in allowed:raise PublicationError('unreviewed stop metadata')
   timestamp(row['at'])
 if 'resource_stops' in tables:
  for row in db.execute('SELECT * FROM resource_stops'):
   if row['reason'] not in {'HTTP 401','HTTP 403','HTTP 404'}:raise PublicationError('unreviewed resource stop')
   timestamp(row['at'])
   if row['task_id'] not in tasks or tasks[row['task_id']]['url']!=row['url']:raise PublicationError('unbacked resource stop')
 if 'policy_exclusions' in tables:
  for row in db.execute('SELECT * FROM policy_exclusions'):
   if row['reason']!='carried scope exclusion':raise PublicationError('unreviewed exclusion reason')
   if row['task_id'] not in tasks or tasks[row['task_id']]['url']!=row['url']:raise PublicationError('unbacked policy exclusion')
 for row in db.execute('SELECT * FROM refreshed_tasks'):
  if row['task_id'] not in tasks:raise PublicationError('unbacked refresh task')
  if row['reason']!='changed API response snapshot; deletions/moves not globally reconciled':raise PublicationError('unreviewed refresh reason')
 for row in db.execute('SELECT observed_at FROM receipts'):timestamp(row[0])
