import argparse
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def main():
    import uvicorn
    from v2.api import create_app
    parser=argparse.ArgumentParser()
    base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
    parser.add_argument('--workspace',default=str(base/'V2工作区'))
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    token=os.environ.get('SPF_API_TOKEN') or secrets.token_urlsafe(32)
    if args.host != '127.0.0.1' and not os.environ.get('SPF_API_TOKEN'):
        parser.error('外部接入需通过 SPF_API_TOKEN 设置访问令牌')
    root=Path(args.workspace)
    root.mkdir(parents=True,exist_ok=True)
    # One process owns a JSON workspace. A crashed process releases the OS lock.
    lock=open(root/'.v2.lock','a+b')
    lock.seek(0)
    if os.name=='nt':
        import msvcrt
        if lock.read(1)==b'':
            lock.write(b'0');lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    app=create_app(root,token)
    sock=socket.socket()
    sock.bind((args.host,args.port))
    port=sock.getsockname()[1]
    if not args.no_browser:
        def open_browser():
            time.sleep(1)
            webbrowser.open(f'http://127.0.0.1:{port}/#token={token}')
        threading.Thread(target=open_browser,daemon=True).start()
    server=uvicorn.Server(uvicorn.Config(app,host=args.host,port=port,log_config=None,access_log=False,timeout_graceful_shutdown=3))
    app.state.shutdown=lambda:setattr(server,'should_exit',True)
    server.run(sockets=[sock])


if __name__=='__main__':
    try:
        main()
    except Exception:
        import traceback
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
        (base/'V2启动错误.log').write_text(traceback.format_exc(),encoding='utf-8')
        if getattr(sys,'frozen',False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,'启动失败。请检查是否已打开同一工作区；详情见程序旁的 V2启动错误.log。','StudentPhotoFlow V2',16)
        raise
