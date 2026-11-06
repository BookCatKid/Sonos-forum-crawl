import copy,json,unittest
from public_payload import project,PublicationError
class PublicPayloadTests(unittest.TestCase):
 def sample(self):return {'url':'https://en.community.sonos.com/contentNavigation/topics','method':'POST','status':200,'request':{'sort':{},'pagination':{'page':1,'pageSize':25}},'response':{'topics':[{'publicId':'1','contentType':'question','content':'<p>Unchanged body $5</p>','author':{'id':'2','username':'public-name','avatar':'unused'},'unreadRepliesCount':7}],'total':1,'authors':{'2':{'id':2,'name':'public-name','signature':'unused','url':'unused'}}}}
 def test_keeps_body_and_attribution_strips_extras(self):
  e=self.sample();p=project(e);self.assertEqual(p['response']['topics'][0]['content'],e['response']['topics'][0]['content']);self.assertEqual(p['response']['authors']['2'],{'id':2,'name':'public-name'});self.assertNotIn('unreadRepliesCount',p['response']['topics'][0]);self.assertEqual(e['response']['authors']['2']['signature'],'unused')
 def test_idempotent(self):self.assertEqual(project(project(self.sample())),project(self.sample()))
 def test_unknown_envelope_field_stops(self):
  e=self.sample();e['cookies']='fixture'
  with self.assertRaises(PublicationError):project(e)
 def test_unknown_profile_field_stops(self):
  e=self.sample();e['response']['authors']['2']['email']='fixture'
  with self.assertRaises(PublicationError):project(e)
 def test_unknown_topic_field_stops(self):
  e=self.sample();e['response']['topics'][0]['newField']='fixture'
  with self.assertRaises(PublicationError):project(e)
 def test_unsupported_route_fails(self):
  e=self.sample();e['url']='https://en.community.sonos.com/events/events';e['method']='GET';e['request']={}
  with self.assertRaises(PublicationError):project(e)
 def test_nested_reply_and_settings(self):
  e=self.sample();e.update(url='https://en.community.sonos.com/topic/1/replies',request={'offset':0,'pageSize':20,'sort':'oldestFirst'})
  child={'metadata':{'totalCount':0,'hasMore':False,'offset':0},'result':[]}
  e['response']={'authors':{},'topicReplyCount':1,'settings':{'userSubscribed':False},'replies':{'metadata':{'totalCount':1,'hasMore':False,'offset':0},'result':[{'id':4,'publicReplyId':5,'authorId':2,'content':'<p>Exact</p>','children':child,'attachments':[],'likedBy':[]}]}}
  p=project(e);self.assertNotIn('settings',p['response']);self.assertEqual(p['response']['replies']['result'][0]['children'],child);self.assertNotIn('likedBy',p['response']['replies']['result'][0])
 def test_empty_authors_list_normalizes_to_empty_map(self):
  e=self.sample();e['response'].update(topics=[],total=0,authors=[]);self.assertEqual(project(e)['response']['authors'],{})
 def test_nonempty_authors_list_not_silently_dropped(self):
  e=self.sample();e['response']['authors']=[{'id':1,'name':'name'}]
  with self.assertRaises(PublicationError):project(e)
if __name__=='__main__':unittest.main()
