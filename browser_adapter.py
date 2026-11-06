"""Import ordinary public browser expansion evidence, separate from HTTP originals."""
import argparse,gzip,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
from mirror import Mirror,safe_url,NOW

def unwrap(value):
 if isinstance(value,dict) and 'content' in value:return json.loads(value['content'])
 return value

def ingest_browser(m,value):
 value=unwrap(value);url=safe_url(value['url']);t=value['topic']
 if not url or value.get('provenance')!='ordinary-public-browser-rendered-dom':raise ValueError('unrecognized public DOM evidence')
 key=urlsplit(url).hostname+':'+str(t['publicId']);posts=value['posts'];ids=[p['publicReplyId'] for p in posts];known=set(ids)
 if len(ids)!=len(known):raise ValueError('duplicate public reply IDs')
 if any(not i.isdigit() for i in ids):raise ValueError('invalid public reply ID')
 if any(p.get('parentPublicReplyId') not in known and p.get('parentPublicReplyId') is not None for p in posts):raise ValueError('missing parent in DOM evidence')
 for p in posts:
  seen={p['publicReplyId']};parent=p.get('parentPublicReplyId');mapping={x['publicReplyId']:x.get('parentPublicReplyId') for x in posts}
  while parent is not None:
   if parent in seen:raise ValueError('parent cycle')
   seen.add(parent);parent=mapping[parent]
 top=sum(p.get('parentPublicReplyId') is None for p in posts)
 consistent=not value.get('remainingMore') and len(posts)==t['allExpected'] and top==t['topExpected']
 audit={'topic':key,'url':url,'page':t['currentPage'],'pages_expected':t['pagesExpected'],'reply_count':len(posts),'top_count':top,'expected_all':t['allExpected'],'expected_top':t['topExpected'],'remaining_more':value.get('remainingMore',[]),'page_count_consistent':consistent,'topic_count_consistent':consistent and t['pagesExpected']==1,'scope':'rendered public DOM at observation time, not independent or global completeness','observed_at':value.get('observedAt')}
 raw=json.dumps(value,ensure_ascii=False,sort_keys=True).encode();sha=hashlib.sha256(raw).hexdigest();path=m.root/'browser_evidence'/(sha+'.json.gz');path.parent.mkdir(exist_ok=True);path.write_bytes(gzip.compress(raw,mtime=0))
 with m.db:
  m.db.executescript('CREATE TABLE IF NOT EXISTS browser_snapshots(topic_key TEXT,url TEXT,sha256 TEXT PRIMARY KEY,path TEXT,observed_at TEXT,audit_json TEXT);CREATE TABLE IF NOT EXISTS browser_posts(sha256 TEXT,public_reply_id TEXT,parent_public_reply_id TEXT,raw_json TEXT,PRIMARY KEY(sha256,public_reply_id));')
  m.db.execute('INSERT OR IGNORE INTO browser_snapshots VALUES(?,?,?,?,?,?)',(key,url,sha,str(path.relative_to(m.root)),value.get('observedAt'),json.dumps(audit)))
  for p in posts:m.db.execute('INSERT OR IGNORE INTO browser_posts VALUES(?,?,?,?)',(sha,p['publicReplyId'],p.get('parentPublicReplyId'),json.dumps(p,ensure_ascii=False)))
 return audit

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();print(json.dumps(ingest_browser(Mirror(a.root),json.loads(a.evidence.read_text())),indent=2))
if __name__=='__main__':main()
