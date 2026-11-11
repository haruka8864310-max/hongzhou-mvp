"""Start the localhost app, verify readiness, then open its own browser URL."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

PROJECT = Path(__file__).resolve().parent
APP_ID = 'hongzhou-metal-market'
UI_CONTRACT = 'decision-dashboard-1'


def read_health(base, expected_data_root=None):
    with urlopen(base+'/api/health',timeout=2) as response:
        result=json.load(response)
    if result.get('app') != APP_ID or result.get('ui_contract') != UI_CONTRACT or result.get('database') != 'ok':
        raise ValueError('連接埠上的程式不是目前版本的弘洲服務')
    if expected_data_root is not None and (not result.get('data_directory') or Path(result['data_directory']).resolve() != Path(expected_data_root).resolve()):
        raise ValueError('目前服務使用不同的資料目錄；不會把驗收資料庫誤開成正式資料庫，請先停止原服務')
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:
        parser.error('連接埠須介於 1024 與 65535')
    base=f'http://127.0.0.1:{args.port}'
    data_root=Path(os.environ.get('HONGZHOU_DATA_DIR') or Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'HongzhouAssistant').expanduser().resolve()
    logs=data_root/'logs';logs.mkdir(parents=True,exist_ok=True)
    with socket.socket() as probe:
        occupied=probe.connect_ex(('127.0.0.1',args.port))==0
    if occupied:
        try:
            read_health(base,data_root)
        except Exception as exc:
            print(f'啟動失敗：{base} 已被其他程式、舊版服務或不同資料目錄占用。請先關閉原服務，再重啟。\n診斷：{exc}',flush=True)
            return 1
        print(f'已確認同版本服務及資料庫正常：{base}',flush=True)
        if not args.no_browser:webbrowser.open(base)
        return 0
    logfile=logs/('server-'+time.strftime('%Y%m%d-%H%M%S')+f'-{os.getpid()}.log')
    process=None
    try:
        with logfile.open('a',encoding='utf-8') as output:
            env={**os.environ,'PYTHONUTF8':'1','PYTHONUNBUFFERED':'1'}
            process=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(args.port)],
                cwd=PROJECT,env=env,stdout=output,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            ready=False
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if process.poll() is not None:break
                try:
                    read_health(base,data_root);ready=True;break
                except Exception:time.sleep(.2)
            if not ready:
                print(f'服務未能啟動，未開啟瀏覽器。日誌：{logfile}',flush=True)
                if process.poll() is None:process.terminate();process.wait(timeout=10)
                print(logfile.read_text(encoding='utf-8',errors='replace')[-5000:],flush=True)
                return 1
            print(f'服務與 SQLite 已就緒：{base}\n資料目錄：{data_root}\n日誌：{logfile}\n請保留此視窗；按 Ctrl+C 關閉服務。',flush=True)
            if not args.no_browser:webbrowser.open(base)
            return process.wait()
    except KeyboardInterrupt:
        print('正在關閉本機服務…',flush=True)
        return 0
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    raise SystemExit(main())
