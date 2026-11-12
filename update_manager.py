"""Persistent, user-triggered source jobs; startup never launches a fetch."""
import json, threading
from fastapi import APIRouter, HTTPException
import operations as ops

router=APIRouter(prefix='/api/updates')
LOCK=threading.Lock()
NAMES={'fenghsin':'豐興週盤','japan':'日本東京製鐵','china':'中國官方旬度鋼價','bls':'美國BLS月度指數','worldbank':'World Bank月均金屬','pcc':'工程會歷史參考價','fx':'三種匯率','news':'中文公告'}

def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS operations_updates(source TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'idle',started_at TEXT,finished_at TEXT,success_at TEXT,added INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,error TEXT,detail TEXT)''')
    for key in NAMES:c.execute('INSERT OR IGNORE INTO operations_updates(source) VALUES(?)',(key,))
    c.execute("UPDATE operations_updates SET state='interrupted',error='上次更新未正常完成；既有行情保留，可再次更新' WHERE state='running'")

def count(c,key):
    if key=='fenghsin':return c.execute('SELECT COUNT(*) FROM fenghsin_facts').fetchone()[0]
    if key=='japan':return c.execute("SELECT COUNT(*) FROM global_quotes WHERE market='jp_tokyo'").fetchone()[0]
    if key=='china':return c.execute("SELECT COUNT(*) FROM global_quotes WHERE market IN ('cn_rebar','cn_hrc')").fetchone()[0]
    if key=='fx':return c.execute("SELECT COUNT(*) FROM market_quotes WHERE instrument LIKE 'fx_%'").fetchone()[0]
    if key=='news':return c.execute('SELECT COUNT(*) FROM market_news').fetchone()[0]
    if key=='pcc':return c.execute("SELECT COUNT(*) FROM metal_observations o JOIN metal_series s ON s.id=o.series_id WHERE s.provider IN ('tw_rebar','tw_plate','tw_hbeam')").fetchone()[0]
    return c.execute('SELECT COUNT(*) FROM metal_observations o JOIN metal_series s ON s.id=o.series_id WHERE s.provider=?',(key,)).fetchone()[0]

@router.get('')
def status():
    with ops.connection() as c:
        result=[]
        for r in c.execute('SELECT * FROM operations_updates'):
            row=dict(r);row.update(name=NAMES[row['source']],total=count(c,row['source']));result.append(row)
        return {'manual_only':True,'sources':result}

def fetch(key):
    import fenghsin,japan_sources,china_sources,market_sources,metal_engine,news_sources
    if key=='fenghsin':return fenghsin.sync(True)
    if key=='japan':return japan_sources.sync(True)
    if key=='china':return china_sources.sync(True)
    if key=='fx':return market_sources.synchronize(True)
    if key=='news':return news_sources.sync(True)
    providers=['tw_rebar','tw_plate','tw_hbeam'] if key=='pcc' else [key]
    return metal_engine.sync(True,providers=providers)

def work(key):
    try:
        with ops.connection() as c:before=count(c,key)
        value=fetch(key)
        failures=[]
        def inspect(v):
            if not isinstance(v,dict):return
            if v.get('ok') is False:failures.append(v.get('message') or '來源更新失敗')
            if v.get('error'):failures.append(str(v['error']))
            for f in v.get('failures',[]):failures.append(str(f))
            for r in v.get('results',[]):inspect(r)
        inspect(value)
        import global_market
        global_market.capture_existing()
        with ops.connection() as c:
            total=count(c,key);stamp=ops.now();error='；'.join(failures) or None
            skipped=bool(value.get('skipped'))
            succeeded=value.get('status',{}).get('success_at') or (stamp if not error and not skipped else None)
            c.execute('UPDATE operations_updates SET state=?,finished_at=?,success_at=COALESCE(?,success_at),added=?,total=?,error=?,detail=? WHERE source=?',('failed' if error else 'cached' if skipped else 'success',stamp,succeeded,total-before,total,error,json.dumps(value,ensure_ascii=False),key))
    except Exception as exc:
        with ops.connection() as c:c.execute("UPDATE operations_updates SET state='failed',finished_at=?,error=? WHERE source=?",(ops.now(),str(exc)[:2000],key))
    finally:LOCK.release()

@router.post('/{key}')
def start(key:str):
    if key not in NAMES:raise HTTPException(404,'來源不存在')
    if not LOCK.acquire(False):raise HTTPException(409,'已有更新正在執行，請等候完成')
    try:
        with ops.connection() as c:c.execute("UPDATE operations_updates SET state='running',started_at=?,finished_at=NULL,error=NULL,added=0 WHERE source=?",(ops.now(),key))
        threading.Thread(target=work,args=(key,),daemon=True,name='hongzhou-manual-update').start()
    except Exception:
        LOCK.release();raise
    return {'state':'running','message':'已開始更新；來源回應前保留舊行情。'}
