"""Independent test-only HTTP service. Never packaged in the application image."""
import base64,json,time,threading,sys
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
state={'delay':4,'fail':False,'calls':[]};lock=threading.Lock()
photo=base64.b64encode(Path(sys.argv[1]).read_bytes()).decode()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  with lock:raw=json.dumps(state).encode()
  self.send_response(200);self.end_headers();self.wfile.write(raw)
 def do_POST(self):
  raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
  if self.path=='/control':
   with lock:state.update(json.loads(raw));data={'ok':True}
  else:
   with lock:delay=state['delay'];fail=state['fail'];state['calls'].append({'started':time.time()})
   time.sleep(delay)
   if fail:data={'error':'SIMULATED Hivision upstream unavailable','token':'mock-secret-do-not-display'}
   else:data={'status':True,'image_base64_standard':photo,'image_base64_hd':photo}
  self.send_response(503 if self.path!='/control' and fail else 200);self.send_header('Content-Type','application/json');self.end_headers()
  try:self.wfile.write(json.dumps(data).encode())
  except (BrokenPipeError,ConnectionResetError):pass
ThreadingHTTPServer(('0.0.0.0',18980),Handler).serve_forever()
