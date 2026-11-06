from html.parser import HTMLParser
from pathlib import Path
import json,time
from run_api import Session
class Props(HTMLParser):
 def __init__(self):super().__init__();self.rows=[]
 def handle_starttag(self,t,a):
  d=dict(a)
  if d.get('data-props'):
   try:self.rows.append({'component':d.get('data-preact'), 'props':json.loads(d['data-props'])})
   except ValueError:pass
