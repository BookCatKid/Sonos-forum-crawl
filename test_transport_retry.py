import tempfile,unittest
from unittest.mock import patch
from run_api import Session,Stop
class RetryTests(unittest.TestCase):
 def test_network_bounded_backoff(self):
  with tempfile.TemporaryDirectory() as d,patch('run_api.time.sleep') as sleep:
   s=Session('de.community.sonos.com',d)
   with patch.object(s,'_request_once',side_effect=[TimeoutError(),OSError(),(b'ok','text/html',200)]) as req:self.assertEqual(s.request('https://de.community.sonos.com/')[0],b'ok');self.assertEqual(req.call_count,3)
   self.assertEqual([c.args[0] for c in sleep.call_args_list],[5,10])
 def test_denial_throttle_challenge_never_retry(self):
  for reason in ['HTTP 403','HTTP 429','challenge page']:
   with tempfile.TemporaryDirectory() as d:
    s=Session('de.community.sonos.com',d)
    with patch.object(s,'_request_once',side_effect=Stop(reason)) as req:
     with self.assertRaises(Stop):s.request('https://de.community.sonos.com/')
     self.assertEqual(req.call_count,1)
 def test_retry_budget_exhaustion(self):
  with tempfile.TemporaryDirectory() as d,patch('run_api.time.sleep'):
   s=Session('de.community.sonos.com',d)
   with patch.object(s,'_request_once',side_effect=TimeoutError()) as req:
    with self.assertRaises(TimeoutError):s.request('https://de.community.sonos.com/')
    self.assertEqual(req.call_count,3)
