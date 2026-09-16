"""Isolated acceptance service: synthetic request overlap, errors and deadlines."""
import base64
import hashlib
import json
import threading
import time
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock=threading.Lock()
state={'active':0,'peak':0,'calls':[],'delay':1,'modes':[]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def output(self,data,status=200):
        raw=data if isinstance(data,bytes) else json.dumps(data).encode()
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        with lock:self.output(state)
    def do_POST(self):
        raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
        if self.path=='/control':
            with lock:
                if state['active']:self.output({'error':'wait for active requests'},409);return
                state.update(active=0,peak=0,calls=[],delay=1,modes=[])
                state.update(json.loads(raw));self.output({'ok':True})
            return
        form=BytesParser(policy=default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\n\r\n').encode()+raw)
        image=next(p.get_payload(decode=True) for p in form.iter_parts() if p.get_param('name',header='content-disposition')=='input_image')
        with lock:
            index=len(state['calls']);mode=state['modes'][index] if index<len(state['modes']) else 'ok'
            call={'index':index,'start':time.time(),'photo_sha256':hashlib.sha256(image).hexdigest(),'mode':mode}
            state['calls'].append(call);state['active']+=1;state['peak']=max(state['peak'],state['active']);delay=state['delay']
        try:
            time.sleep(5.5 if mode=='timeout' else delay)
            if mode=='error':self.output({'error':'SIMULATED service unavailable'},503)
            elif mode=='parse':self.output(b'{invalid json')
            else:self.output({'status':True,'image_base64_standard':base64.b64encode(image).decode()})
        except (BrokenPipeError,ConnectionResetError):pass
        finally:
            with lock:call['end']=time.time();state['active']-=1


if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8768),Handler).serve_forever()
