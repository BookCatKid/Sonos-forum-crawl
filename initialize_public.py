"""Offline-only approved-plan initialization. Does not upload or collect."""
import json,shutil
from pathlib import Path
from controller import prepare
from public_store import check_public
from public_plan import validate_pinned

def initialize(root):
 root=Path(root)
 if root.exists():raise ValueError('initial target must not exist')
 base=Path(__file__).parent;plan=json.loads((base/'approved-discovery-plan.json').read_text());validate_pinned(plan)
 root.mkdir(parents=True)
 try:
  shutil.copyfile(base/'scope.json',root/'scope.json');shutil.copyfile(base/'approved-discovery-plan.json',root/'discovery-plan.json');prepare(root,root/'discovery-plan.json');check_public(root)
 except Exception:
  # Do not remove a partial initialized root automatically. It cannot qualify as
  # a verified source generation; evidence remains for operator review.
  raise
 return {'offline_initialization':True,'categories':len(plan['categories']),'complete':False}
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();print(json.dumps(initialize(a.root)))
