"""Schema-only public projection. Never keyword-scan or analyze post text.
Unknown fields fail closed. Profile extras are intentionally not mirrored.
"""
from urllib.parse import urlsplit,parse_qsl
from api_adapter import validate

class PublicationError(ValueError):pass

def fields(value,keep,drop=()):
 if not isinstance(value,dict):raise PublicationError('expected object')
 unknown=set(value)-set(keep)-set(drop)
 if unknown:raise PublicationError('unreviewed fields: '+','.join(sorted(unknown)))
 return {k:value[k] for k in keep if k in value}

TOPIC='id publicId contentType content title topicUrl author categoryId categoryName categoryUrl publishedAt lastActivityAt replyCount inclusiveVisibleReplyCount hasBestAnswer lastPostId lastReplyPublicId'.split()
TOPIC_DROP='avatar bugStatus ideaStatus featuredImage featuredImageAltText isNew isUnreadPostCountEnabled lastActivityAtRelative lastContributor likedOrVotedByCurrentUser likes productAreas publicLabel publishedAtDisplayText publishedAtRelative sticky timestampDisplayText unreadRepliesCount views votes'.split()
REPLY='id publicReplyId authorId content children repliedAt inclusiveReplyCount inclusiveVisibleReplyCount isBestAnswer isPinnedReply url'.split()
REPLY_DROP='attachments highlighted likedBy repliedAtDisplayText repliedAtTooltipText topLikedBy'.split()
META='depthLevel hasMore hasPrevious limit offset page pageSize totalCount'.split()
AUTHOR_DROP='avatar badges isBanned postCount rank url userLevel userTitle likesReceived repliesMade showBadges showReputation signature'.split()

def scalar(obj,key,types):
 if key in obj and type(obj[key]) not in types:raise PublicationError('invalid field type: '+key)

def identity(obj,key):
 if key in obj and (type(obj[key]) not in (int,str) or not str(obj[key]).isdigit()):raise PublicationError('invalid public identity: '+key)

def public_url(obj,key):
 if key in obj:
  value=obj[key]
  if key=='topicUrl':value=fields(value,['destination'])['destination']
  if type(value)!=str:raise PublicationError('invalid public URL type')
  p=urlsplit(value)
  from mirror import HOSTS
  if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password or (p.query and (key!='url' or any(k!='postid' or not v.isdigit() for k,v in parse_qsl(p.query)))) or (p.fragment and not (key=='url' and p.fragment.startswith('post') and p.fragment[4:].isdigit())):raise PublicationError('invalid public content URL')

def author(a):
 a=fields(a,['id','name','username'],AUTHOR_DROP);identity(a,'id')
 for k in ('name','username'):scalar(a,k,(str,))
 return a
def topic(t):
 t=fields(t,TOPIC,TOPIC_DROP)
 if 'author' in t:
  try:t['author']=author(t['author'])
  except PublicationError as e:raise PublicationError('topic.author: '+str(e)) from None
 for k in ('id','publicId','categoryId','lastPostId','lastReplyPublicId'):
  if t.get(k) not in (None,''):identity(t,k)
 for k in ('content','title','categoryName','publishedAt','lastActivityAt'):scalar(t,k,(str,))
 for k in ('topicUrl','categoryUrl'):public_url(t,k)
 if t.get('contentType') not in ('article','question','conversation','idea','productUpdate'):raise PublicationError('unreviewed topic type')
 for k in ('replyCount','inclusiveVisibleReplyCount'):scalar(t,k,(int,))
 scalar(t,'hasBestAnswer',(bool,))
 return t

def replies(c):
 c=fields(c,['metadata','result']);c['metadata']=fields(c['metadata'],META)
 for k,v in c['metadata'].items():
  if k in ('hasMore','hasPrevious'):scalar(c['metadata'],k,(bool,))
  elif type(v)!=int or v<0:raise PublicationError('invalid pagination metadata')
 out=[]
 for raw in c['result']:
  if raw.get('attachments'):raise PublicationError('nonempty attachment metadata schema awaits review')
  r=fields(raw,REPLY,REPLY_DROP)
  for k in ('id','publicReplyId','authorId'):identity(r,k)
  for k in ('content','repliedAt'):scalar(r,k,(str,))
  for k in ('inclusiveReplyCount','inclusiveVisibleReplyCount'):scalar(r,k,(int,))
  for k in ('isBestAnswer','isPinnedReply'):scalar(r,k,(bool,))
  public_url(r,'url')
  if r.get('children') is not None:r['children']=replies(r['children'])
  out.append(r)
 c['result']=out
 return c

def public_request(url,method,request):
 p=validate(url,method,request)
 if method=='POST' and (p.query or p.fragment):raise PublicationError('unexpected public POST URL parameters')
 if p.path=='/contentNavigation/topics':
  r=fields(request,['sort','filters','pagination','stripHtmlFromContent','trimContentLength','enableLanguageBasedCategoryFilter','containerIds'])
  fields(r['sort'],['sticky','lastActivityAt'])
  if any(v not in ('asc','desc') for v in r['sort'].values()):raise PublicationError('unreviewed sort')
  fields(r['pagination'],['page','pageSize'])
  filters=fields(r.get('filters',{}),['contentType','productArea','category'])
  for key,values in filters.items():
   if not isinstance(values,list):raise PublicationError('invalid filter')
   if key=='contentType':
    if any(v not in ('article','conversation','question','idea','productUpdate') for v in values):raise PublicationError('unreviewed content type')
   elif any(type(v)!=int or v<=0 for v in values):raise PublicationError('unreviewed category/product filter')
  if any(type(v)!=int or v<=0 for v in r.get('containerIds',[])):raise PublicationError('invalid container')
  if r.get('stripHtmlFromContent',False) is not False or r.get('trimContentLength',0)!=0:raise PublicationError('truncated content request')
 elif p.path.startswith('/topic/'):
  r=fields(request,['offset','pageSize','sort','parentId'])
  if r.get('parentId'):fields(r['parentId'],['id','contentType'])
 elif p.path in ('/categoryV2/listReadableCategoryOrderTopics','/categoryV2/listReadableCategoryContent','/categoryV2/getReadableCuratedContent'):
  r=fields(request,['categoryId','type','pagination'])
  if type(r['categoryId'])!=int or r['categoryId']<=0:raise PublicationError('invalid public category')
  if 'type' in r and r['type'] is not None:raise PublicationError('unreviewed category type')
  if 'pagination' in r:fields(r['pagination'],['page','pageSize'])
 elif p.path=='/categoryV2/getAccessibleCategoriesTree':
  r=fields(request,['excludeFields','type'])
  if any(v not in ('description','thumbnailImage') for v in r.get('excludeFields',[])):raise PublicationError('unreviewed excluded tree fields')
 elif p.path in ('/productUpdates/topics','/events/events'):
  if request:raise PublicationError('unexpected GET body')
 else:raise PublicationError('unreviewed public request route')
 return p

def category(c):
 c=fields(c,['children','id','isContainer','isKnowledgeBase','parentId','supportedContentTypes','title','type','url','visibleTopicsCount','containerCategoriesCount','contentCategoriesCount','language'],['bannerConfig','heroImage'])
 for k in ('id','parentId'):
  if c.get(k) is not None:identity(c,k)
 for k in ('title','language'):scalar(c,k,(str,))
 for k in ('isContainer','isKnowledgeBase'):scalar(c,k,(bool,))
 for k in ('type','visibleTopicsCount','containerCategoriesCount','contentCategoriesCount'):scalar(c,k,(int,))
 if 'supportedContentTypes' in c and (not isinstance(c['supportedContentTypes'],list) or any(v not in ('question','conversation','article','idea','productUpdate') for v in c['supportedContentTypes'])):raise PublicationError('unreviewed category content types')
 public_url(c,'url')
 c['children']=[category(x) for x in c.get('children',[])]
 return c

def event(e):
 e=fields(e,['id','title','content','startAt','endAt','type','location','destinationUrl','timezoneName'],['link','linkLabel','image','isOpenForSignUp','isLoggedInUserAttendee','confirmationMessage','attendeeCount','attendees','externalRegistrationUrl','externalRegistrationUrlLabel','featuredTopics','userGroupId','createdBy','createdAt'])
 identity(e,'id')
 for k in ('title','content','location','timezoneName'):scalar(e,k,(str,type(None)))
 for k in ('startAt','endAt'):scalar(e,k,(int,type(None)))
 scalar(e,'type',(str,int));public_url(e,'destinationUrl')
 return e

def project(e):
 e=fields(e,['url','method','request','status','response'],['headerNames','observed_at'])
 e['request']=e.get('request') or {};p=public_request(e['url'],e['method'],e['request'])
 if type(e['status'])!=int or e['status']!=200:raise PublicationError('only successful public JSON receipts publish')
 b=e['response']
 if p.path=='/contentNavigation/topics':
  b=fields(b,['topics','authors','total']);b['topics']=[topic(t) for t in b['topics']]
 elif p.path=='/categoryV2/getAccessibleCategoriesTree':
  b=fields(b,['0']);b={k:[category(c) for c in v] for k,v in b.items()}
 elif p.path in ('/categoryV2/listReadableCategoryOrderTopics','/categoryV2/listReadableCategoryContent','/categoryV2/getReadableCuratedContent'):
  b=fields(b,['result']);r=fields(b['result'],['topics','total','title'])
  if isinstance(r['topics'],dict):
   if any(not str(k).isdigit() for k in r['topics']):raise PublicationError('invalid ordered category key')
   r['topics']={k:[topic(t) for t in v] for k,v in r['topics'].items()}
  elif isinstance(r['topics'],list):r['topics']=[topic(t) for t in r['topics']]
  else:raise PublicationError('invalid category topics shape')
  scalar(r,'total',(int,));scalar(r,'title',(str,));b['result']=r
 elif p.path=='/events/events':
  b=fields(b,['pastEvents','upcomingEvents','totalPageCountPast','totalPageCountUpcoming'])
  b['pastEvents']=[event(x) for x in b['pastEvents']];b['upcomingEvents']=[event(x) for x in b['upcomingEvents']]
  for k in ('totalPageCountPast','totalPageCountUpcoming'):scalar(b,k,(int,))
 elif p.path=='/productUpdates/topics':
  b=fields(b,['topics','totalPageCount','totalTopicCount']);b['topics']=[topic(t) for t in b['topics']]
 elif p.path.startswith('/topic/') and p.path.endswith('/replies'):
  b=fields(b,['authors','replies','topicReplyCount'],['settings']);b['replies']=replies(b['replies'])
 else:raise PublicationError('route publication schema awaits review: '+p.path)
 for k in ('total','topicReplyCount','totalPageCount','totalTopicCount'):
  if k in b and (type(b[k])!=int or b[k]<0):raise PublicationError('invalid total count')
 if 'authors' in b:
  if not isinstance(b['authors'],dict):raise PublicationError('expected author map')
  out={}
  for k,a in b['authors'].items():
   try:out[k]=author(a)
   except PublicationError as err:raise PublicationError('authors-map: '+str(err)) from None
  b['authors']=out
 e['response']=b
 return e
