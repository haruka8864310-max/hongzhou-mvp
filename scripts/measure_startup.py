"""Compare installed-dependency BAT checks plus localhost readiness, offline.

Uses only cloned test SQLite, never the formal directory. The baseline app is
read from Git into memory, not installed as another project.
"""
import argparse,json,os,sqlite3,statistics,subprocess,sys,time
from pathlib import Path
from urllib.request import urlopen

def main():
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--runs',type=int,default=3);p.add_argument('--baseline-ref',default='bb4d031');args=p.parse_args()
    project=Path(__file__).resolve().parents[1];target=project/'.test-runs'/'startup-comparison'/str(time.time_ns());target.mkdir(parents=True,exist_ok=False)
    source=Path(args.database).resolve()
    if '.test-runs' not in source.parts:raise ValueError('只能讀取明確的獨立測試資料庫')
    baseline=subprocess.check_output(['git','show',args.baseline_ref+':app.py'],cwd=project).decode('utf-8')
    oldcheck='import fastapi,uvicorn,multipart,pydantic_core,openpyxl,certifi,pypdf,skforecast,lightgbm'
    newcheck="import importlib.util; assert all(importlib.util.find_spec(m) for m in ['fastapi','uvicorn','multipart','pydantic_core','openpyxl','certifi','pypdf','skforecast','lightgbm','requests'])"
    result={}
    for mode,check,port in [('before',oldcheck,18905),('after',newcheck,18906)]:
        values=[];root=target/mode;root.mkdir(exist_ok=True)
        if (root/'hongzhou.db').exists():raise ValueError('請保留前次結果，使用新的驗收工作目錄後再執行')
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(root/'hongzhou.db') as dest:src.backup(dest)
        env={**os.environ,'HONGZHOU_DATA_DIR':str(root),'HONGZHOU_DISABLE_SCHEDULER':'1','PYTHONUTF8':'1'}
        code=("import sys,types; m=types.ModuleType('app');m.__file__="+repr(str(project/'app.py'))+";sys.modules['app']=m;exec(compile("+repr(baseline)+",m.__file__,'exec'),m.__dict__);import uvicorn;uvicorn.run(m.app,host='127.0.0.1',port="+str(port)+",log_level='error')") if mode=='before' else None
        for _ in range(args.runs):
            start=time.perf_counter();subprocess.run([sys.executable,'-c',check],env=env,cwd=project,check=True,stdout=subprocess.DEVNULL)
            command=[sys.executable,'-c',code] if code else [sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--log-level','error']
            process=subprocess.Popen(command,cwd=project,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                while time.perf_counter()-start<40:
                    if process.poll() is not None:raise RuntimeError('測量服務提早結束')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=.2) as response:health=json.load(response)
                        if Path(health['data_directory'])!=root:raise RuntimeError('量測連到非預期服務')
                        values.append(round(time.perf_counter()-start,3));break
                    except OSError:time.sleep(.03)
                else:raise TimeoutError('測量逾時')
            finally:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else:process.terminate()
                process.wait(10)
        result[mode]={'seconds':values,'median':statistics.median(values)}
    result['reduction_percent']=round((1-result['after']['median']/result['before']['median'])*100,2)
    (target/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
