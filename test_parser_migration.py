import unittest,tempfile,sqlite3,json
from pathlib import Path
from parser_migration import apply,FIXES
class MigrationTests(unittest.TestCase):
 def seed(self,p,reason=None):
  db=sqlite3.connect(p/'api.sqlite');db.executescript('CREATE TABLE controller(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE transport_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT);CREATE TABLE tasks(url TEXT,request TEXT,state TEXT)')
  for h,(r,at,c) in FIXES.items():db.execute('INSERT INTO transport_stops VALUES(?,?,?)',(h,reason or r,at));db.execute('INSERT INTO tasks VALUES(?,?,?)',('https://'+h+'/contentNavigation/topics',json.dumps({'filters':{'category':[c]},'pagination':{'page':1}}),'pending'))
  db.commit();db.close()
 def test_only_exact_parser_stops_removed_once(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);self.assertEqual(len(apply(p)),2);self.assertEqual(apply(p),[])
 def test_http_stops_never_reset(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p,'HTTP 403');self.assertEqual(apply(p),[]);db=sqlite3.connect(p/'api.sqlite');self.assertEqual(db.execute('SELECT count(*) FROM transport_stops').fetchone()[0],2)
if __name__=='__main__':unittest.main()
