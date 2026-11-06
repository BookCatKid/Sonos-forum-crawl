"""Prepare one queued public read for browser execution, without starting it."""
import argparse,json
from pathlib import Path
from api_adapter import APIStore,validate
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--host',required=True);p.add_argument('--script-output',type=Path,required=True);a=p.parse_args();store=APIStore(a.root)
rows=store.db.execute("SELECT * FROM tasks WHERE state='pending' ORDER BY rowid")
row=next((r for r in rows if r['url'].startswith('https://'+a.host+'/')),None)
if row is None:raise SystemExit('No pending task for this host')
task={'url':row['url'],'request':json.loads(row['request'])};validate(task['url'],'GET' if '/productUpdates/topics' in task['url'] or '/events/events' in task['url'] else 'POST',task['request']);template=(Path(__file__).parent/'api_read_once.js').read_text();a.script_output.write_text(template.replace('const task=TASK;','const task='+json.dumps(task)+';'));print(json.dumps({'task_id':row['id'],'script':str(a.script_output),'url':task['url'],'executed':False}))
