import os,sys,json
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from urllib.parse import urlsplit,unquote
PROJECT=Path.cwd();sys.path.insert(0,str(PROJECT))
os.environ['DJANGO_SETTINGS_MODULE']='flexs_project.settings.test'
import django
django.setup()
from django.template import Context
from django.template.loader import get_template
from django.contrib.staticfiles import finders
context={'active_theme':'blueprint','user':SimpleNamespace(is_authenticated=True,is_staff=False,username='Vista previa'),
 'all_diameter_options_json':json.dumps(['7/16','1/2','3/4']),'laminated_diameter_options_json':json.dumps(['1/2','3/4']),
 'diameter_options':['7/16','1/2','3/4'],'profile_options':['CURVA','SEMICURVA','PLANA'],
 'form_values':{'clamp_type':'trefilada','diameter':'1/2','profile_type':'CURVA','width_mm':120,'length_mm':180},
 'site_settings':SimpleNamespace(company_email='ventas@flexs.com.ar',company_phone='+54 011 5177-9690')}
pages={path:get_template(template).template.render(Context(context)).encode() for path,template in [('/catalogo/como-medir/','catalog/how_to_measure.html')]}
class Handler(SimpleHTTPRequestHandler):
 def do_GET(self):
  path=urlsplit(self.path).path
  if path in pages:
   self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(get_template('catalog/how_to_measure.html').template.render(Context(context)).encode());return
  if path=='/pedidos/carrito/count/':
   self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"count":0}');return
  super().do_GET()
 def translate_path(self,path):
  name=unquote(urlsplit(path).path).removeprefix('/static/')
  if '..' in Path(name).parts:return str(PROJECT/'output/measurement-guide-review/missing')
  return finders.find(name) or str(PROJECT/'output/measurement-guide-review/missing')
print('DIMENSIONS PREVIEW http://127.0.0.1:8779',flush=True)
ThreadingHTTPServer(('127.0.0.1',8779),Handler).serve_forever()
