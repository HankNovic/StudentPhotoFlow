import os
from pathlib import Path
import uvicorn
from v2.api import create_app
from v2.version import VERSION
root=Path(os.getenv('SPF_WORKSPACE','/data/workspace')).resolve();root.mkdir(parents=True,exist_ok=True)
token=os.getenv('SPF_API_TOKEN','').strip()
if not token: raise SystemExit('SPF_API_TOKEN is required; set it in .env')
app=create_app(root,token);app.state.docker_mode=True;app.state.shutdown=None
@app.get('/healthz',include_in_schema=False)
def healthz(): return {'status':'ok','version':VERSION}
if __name__=='__main__': uvicorn.run(app,host=os.getenv('SPF_HOST','0.0.0.0'),port=int(os.getenv('SPF_PORT','8769')),log_level=os.getenv('SPF_LOG_LEVEL','info'))
