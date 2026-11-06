"""Run one bounded API-first category sample and selected topic's reply traversal."""
import argparse,json
from pathlib import Path
from api_adapter import APIStore
from run_api import run

def sample(root,planfile,host,category,max_reply_requests):
 if not 1<=max_reply_requests<=5:raise ValueError('sample reply cap1..5')
 plan=json.loads(Path(planfile).read_text());entry=next((c for c in plan['categories'] if c['host']==host and c['id']==category and not c['container']),None)
 if not entry:raise ValueError('category not evidenced in discovery plan')
 if entry['needs_subpartition']:raise ValueError('select a verified typepartition before sampling oversized leaf')
 store=APIStore(root)
 if store.db.execute('SELECT count(*) FROM tasks').fetchone()[0]:raise ValueError('sample requires fresh task store so selection cannot consume an older pending task')
 req=entry['first_request'];store.task(req['url'],req['request'],'sample-first-category-page');store.db.commit();first=run(root,host,1)
 if first.get('stopped'):return {'listing':first,'complete':False}
 # A concrete sample, not bulk traversal: prefer first topic with nonzero replies.
 rows=list(store.db.execute('SELECT public_id,raw_json FROM topic_rows WHERE host=? ORDER BY rowid',(host,)));selected=next((r for r in rows if json.loads(r['raw_json']).get('replyCount',0)>0),rows[0] if rows else None)
 if selected is None:return {'listing':first,'complete':False,'gap':'no topic in category response'}
 # Defer listing continuation, never follow a whole-category crawl in sample mode.
 with store.db:store.db.execute("UPDATE tasks SET state='deferred' WHERE state='pending'")
 store.plan_replies(host,selected['public_id']);result=run(root,host,max_reply_requests)
 return {'category':category,'selected_topic':selected['public_id'],'listing':first,'replies':result,'complete':False,'scope':'single category page plus one topic bounded reply walk, not wholeforum'}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);p.add_argument('--host',required=True);p.add_argument('--category',type=int,required=True);p.add_argument('--reply-request-cap',type=int,default=5);a=p.parse_args();print(json.dumps(sample(a.root,a.plan,a.host,a.category,a.reply_request_cap),indent=2))
