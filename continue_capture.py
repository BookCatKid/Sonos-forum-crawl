"""One bounded successor dispatch, using only this repository's job token."""
import datetime,json,os,time,urllib.request

def continue_once(api,run_id,result,now=lambda:datetime.datetime.now(datetime.timezone.utc),sleep=time.sleep):
 if result.get('paused'):return {'chained':False,'reason':'all hosts stopped'}
 run=api('GET','/actions/runs/'+str(run_id))
 if run.get('id')!=int(run_id) or run.get('path')!='.github/workflows/capture.yml':raise ValueError('unexpected originating workflow')
 start=datetime.datetime.fromisoformat(run['created_at'].replace('Z','+00:00'));wait=max(0,(start+datetime.timedelta(minutes=30)-now()).total_seconds())
 if wait>1800:raise ValueError('invalid continuation clock')
 if wait:sleep(wait)
 rows=api('GET','/actions/workflows/capture.yml/runs?per_page=100')['workflow_runs']
 if any(str(r['id'])!=str(run_id) and r['status']!='completed' for r in rows):return {'chained':False,'reason':'successor already queued or active'}
 api('POST','/actions/workflows/capture.yml/dispatches',{'ref':'main'})
 return {'chained':True,'reason':'one eligible bounded successor dispatched'}

if __name__=='__main__':
 from github_release import ReleaseTransport
 remote=ReleaseTransport(os.environ['REPOSITORY'])
 if remote.repo!='BookCatKid/Sonos-forum-crawl':raise ValueError('wrong repository')
 def api(method,path,data=None):
  raw=remote.request(method,remote._api(path),None if data is None else json.dumps(data).encode())
  return json.loads(raw) if raw else {}
 import sys
 try:
  with open(sys.argv[1]) as f:result=json.load(f)
  if not isinstance(result,dict):raise ValueError('result not object')
 except (OSError,ValueError):result={'recovery_required':True}
 print(json.dumps(continue_once(api,os.environ['RUN_ID'],result)))
