import io,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from run_api import Session,Stop
class DenialEvidenceTests(unittest.TestCase):
 def test_retains_only_safe_diagnostics_and_never_retries_denial(self):
  with tempfile.TemporaryDirectory() as d:
   s=Session('de.community.sonos.com',d)
   e=urllib.error.HTTPError('https://de.community.sonos.com/restricted?secret=not-retained',403,'Forbidden',{'Content-Type':'text/html','Server':'edge','CF-Ray':'diagnostic-id','Set-Cookie':'secret','Location':'/login?secret=not-retained'},io.BytesIO(b''))
   with patch.object(s.opener,'open',side_effect=e) as req:
    with self.assertRaises(Stop):s.request('https://de.community.sonos.com/restricted')
    self.assertEqual(req.call_count,1)
   event=json.loads((Path(d)/'transport-events.jsonl').read_text());self.assertEqual(event['final_url'],'https://de.community.sonos.com/restricted');self.assertEqual(event['response_headers'],{'Content-Type':'text/html','Server':'edge','CF-Ray':'diagnostic-id'});self.assertNotIn('secret',json.dumps(event))
