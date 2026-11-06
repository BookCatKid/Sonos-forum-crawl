import unittest
from public_payload import replies,PublicationError,attachment_metadata
class AttachmentTests(unittest.TestCase):
 def sample(self):return {'type':'image','source':'opaque-public-reference','source_thumb':'opaque-thumb','file_uuid':None,'file_original_name':'','id':'123'}
 def test_reference_metadata_preserved_without_fetch(self):
  a=self.sample();r=replies({'metadata':{},'result':[{'id':'1','publicReplyId':'2','content':'body','attachments':[a]}]});self.assertEqual(r['result'][0]['attachments'],[a])
 def test_video_http_source_is_data_not_action(self):
  a=self.sample();a.update(type='video',source='https://www.youtube.com/watch?v=example');self.assertEqual(attachment_metadata(a),a)
 def test_unknown_field_rejected(self):
  a=self.sample();a['private']='no'
  with self.assertRaises(PublicationError):attachment_metadata(a)
 def test_unknown_type_and_uuid_rejected(self):
  for k,v in [('type','file'),('file_uuid','unreviewed')]:
   a=self.sample();a[k]=v
   with self.assertRaises(PublicationError):attachment_metadata(a)
