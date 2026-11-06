import json,sqlite3,tempfile,unittest
from pathlib import Path
from api_adapter import APIStore
from checkpoint import export_checkpoint,restore_checkpoint

class CheckpointTests(unittest.TestCase):
 def seed(self,root):
  s=APIStore(root);s.ingest({'url':'https://en.community.sonos.com/contentNavigation/topics','method':'POST','status':200,'request':{'sort':{},'pagination':{'page':1,'pageSize':25}},'response':{'topics':[{'publicId':'1','contentType':'question','content':'<p>body</p>'}],'total':1}})
  s.db.execute('CREATE TABLE transport_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT)');s.db.execute("INSERT INTO transport_stops VALUES('de.community.sonos.com','HTTP 403','fixture')");s.db.commit();s.db.close()
  (root/'scope.json').write_text('{"excluded_ids":["6935006"]}')
 def test_empty_machine_restore_preserves_receipts_queue_and_stops(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p/'run');export_checkpoint(p/'run',p/'generation');restore_checkpoint(p/'generation',p/'restored')
   a=sqlite3.connect(p/'run/api.sqlite');b=sqlite3.connect(p/'restored/api.sqlite')
   for table in ('tasks','topic_rows','receipts','transport_stops'):
    self.assertEqual(a.execute('SELECT * FROM '+table).fetchall(),b.execute('SELECT * FROM '+table).fetchall())
   self.assertEqual((p/'run/scope.json').read_bytes(),(p/'restored/scope.json').read_bytes())
 def test_corruption_fails_closed_and_does_not_create_target(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p/'run');export_checkpoint(p/'run',p/'generation');next((p/'generation/json').rglob('*.gz')).write_bytes(b'bad')
   with self.assertRaises(ValueError):restore_checkpoint(p/'generation',p/'restored')
   self.assertFalse((p/'restored').exists())
 def test_existing_destination_never_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p/'run');export_checkpoint(p/'run',p/'generation')
   with self.assertRaises(ValueError):export_checkpoint(p/'run',p/'generation')
   with self.assertRaises(ValueError):restore_checkpoint(p/'generation',p/'run')
 def test_parent_path_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p/'run');export_checkpoint(p/'run',p/'generation');m=json.loads((p/'generation/manifest.json').read_text());m['files'][0]['path']='../api.sqlite';(p/'generation/manifest.json').write_text(json.dumps(m))
   with self.assertRaises(ValueError):restore_checkpoint(p/'generation',p/'restore')
 def test_session_files_excluded(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p/'run');(p/'run/cookies.json').write_text('fixture');export_checkpoint(p/'run',p/'generation');self.assertFalse((p/'generation/cookies.json').exists())
if __name__=='__main__':unittest.main()
