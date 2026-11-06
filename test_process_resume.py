"""Real process death, simulated read-only network. Never contacts Sonos."""
import json,os,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
class ProcessResumeTests(unittest.TestCase):
 def test_sigkill_preserves_committed_task_and_retries_only_unfinished_read(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'run';marker=Path(d)/'inflight';plan=Path(d)/'plan'
   req=lambda n:{'url':'https://en.community.sonos.com/contentNavigation/topics','request':{'sort':{},'pagination':{'page':n,'pageSize':25}}}
   plan.write_text(json.dumps({'categories':[{'container':False,'first_request':req(n)} for n in [1,2]]}))
   env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent.resolve()))
   def proc(code):return subprocess.run([sys.executable,'-c',code,str(root),str(plan),str(marker)],env=env,capture_output=True,text=True,check=True)
   proc('import sys;from controller import prepare;prepare(sys.argv[1],sys.argv[2])')
   code='''import sys,time
from pathlib import Path
from controller import run_batch
from test_controller import Fake
from unittest.mock import patch
class Interrupted(Fake):
 def read(self,url,p):
  if p['pagination']['page']==2:
   Path(sys.argv[3]).write_text('read in flight')
   while True:time.sleep(.02)
  return super().read(url,p)
with patch('controller.time.sleep'):
 run_batch(sys.argv[1],2,session_factory=Interrupted)
'''
   child=subprocess.Popen([sys.executable,'-c',code,str(root),str(plan),str(marker)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   try:
    deadline=time.monotonic()+8
    while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
    self.assertTrue(marker.exists(),'child never reached second in-flight read');child.kill();child.wait(timeout=3);self.assertEqual(child.returncode,-signal.SIGKILL)
   finally:
    if child.poll() is None:child.kill();child.wait()
    child.stderr.close()
   r=proc('''import sys,json
from controller import run_batch
from test_controller import Fake
from unittest.mock import patch
with patch('controller.time.sleep'):r=run_batch(sys.argv[1],2,session_factory=Fake)
print(json.dumps(r))
''');r=json.loads(r.stdout);self.assertEqual(r['captured_this_batch'],1);self.assertEqual(r['status']['tasks'],{'captured':2});self.assertEqual(r['status']['receipts'],2)
