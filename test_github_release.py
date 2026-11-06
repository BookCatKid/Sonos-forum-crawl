import json,unittest
from github_release import ReleaseTransport
from generations import RemoteError
class ReleaseTests(unittest.TestCase):
 def test_exact_repo_upload_and_asset_download(self):
  calls=[];assets=[];content={}
  def req(method,url,data=None,ctype='application/json'):
   calls.append((method,url,ctype))
   if '/releases/tags/' in url:raise FileNotFoundError()
   if method=='POST' and url.endswith('/releases'):return b'{"id":7}'
   if method=='GET' and '/assets?' in url:return json.dumps(assets).encode()
   if method=='POST' and 'uploads.github.com/' in url:
    name=url.split('name=')[1];assets.append({'id':len(assets)+1,'name':name});content[len(assets)]=data;return b'{}'
   if method=='GET' and '/releases/assets/' in url:return content[int(url.split('/')[-1])]
   raise AssertionError((method,url))
  r=ReleaseTransport('BookCatKid/Sonos-forum-crawl','fixture',req);self.assertFalse(r.exists('gen'));r.create('gen');r.put('gen','index.json',b'{}');self.assertEqual(r.get('gen','index.json'),b'{}')
  with self.assertRaises(RemoteError):r.put('gen','index.json',b'changed')
  self.assertTrue(all('/BookCatKid/Sonos-forum-crawl/' in u for _,u,_ in calls));self.assertEqual(calls[-2][2],'download')
 def test_bad_repository_or_missing_token(self):
  with self.assertRaises(ValueError):ReleaseTransport('../bad','fixture')
  with self.assertRaises(RemoteError):ReleaseTransport('owner/repo','')
 def test_generation_and_asset_paths_rejected(self):
  r=ReleaseTransport('owner/repo','fixture',lambda *a:b'{"id":1}')
  with self.assertRaises(ValueError):r.exists('../other')
  with self.assertRaises(ValueError):r.put('gen','../file',b'x')
if __name__=='__main__':unittest.main()
