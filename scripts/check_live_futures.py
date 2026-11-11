"""Bounded AKShare public endpoint probe; never writes the production database.

Usage: .venv\\Scripts\\python.exe scripts/check_live_futures.py
Only audit summaries are persisted until provider reuse rights are confirmed.
AKShare MIT is a software license, not a license for Sina/exchange market data.
"""
import json,sys,time
from datetime import datetime,timezone
from pathlib import Path
import akshare as ak
from akshare.utils import http

SYMBOLS=('RB0','I0','HC0')  # AKShare official futures documentation, not guessed codes.
DOC='https://akshare.akfamily.xyz/data/futures/futures.html'
TERMS='https://finance.sina.com.cn/roll/2021-05-12/doc-ikmxzfmm2033220.shtml'
# Reviewed after the initial bounded availability probe: sections 2.5, 2.7, 6.1,
# 10.1 do not authorize this company's automated download/storage/display use.
# Do not turn this into an end-user checkbox. Provider permission needs review.
SINA_REUSE_ALLOWED=False


def probe(symbol):
    if symbol not in SYMBOLS:raise ValueError('Unsupported symbol')
    if not SINA_REUSE_ALLOWED:
        return {'symbol':symbol,'ok':False,'error':'Provider authorization required before further requests',
                'terms':TERMS,'http_status':None,'attempts':[]}
    attempts=[];original=http.get
    def audited(url,*args,**kwargs):
        kwargs['timeout']=20
        item={'url':url,'params':kwargs.get('params'),'http_status':None}
        attempts.append(item)
        try:
            response=original(url,*args,**kwargs)
            item.update(url=response.url,http_status=response.status_code,content_type=response.headers.get('content-type'))
            response.raise_for_status()
            return response
        except Exception as exc:
            item['error']=str(exc)[:500]
            raise
    http.get=audited
    result={'symbol':symbol,'function':'futures_zh_daily_sina','documentation':DOC,
            'akshare_version':ak.__version__,'checked_at':datetime.now(timezone.utc).isoformat(),
            'attempts':attempts,'provider_reuse':'unconfirmed; no production import or training'}
    try:
        frame=ak.futures_zh_daily_sina(symbol=symbol)
        import pandas as pd
        frame['date']=pd.to_datetime(frame['date'],errors='raise')
        frame=frame.sort_values('date')
        if frame.empty or frame['date'].duplicated().any():raise ValueError('Empty or duplicate dates')
        if frame['date'].max().date()>datetime.now().date():raise ValueError('Future dated observations')
        if not frame['close'].gt(0).all():raise ValueError('Invalid closing prices')
        result.update(ok=True,rows=len(frame),first_date=str(frame.iloc[0]['date'].date()),
                      latest_date=str(frame.iloc[-1]['date'].date()),latest_close=float(frame.iloc[-1]['close']),
                      currency='CNY',unit='yuan/tonne',frequency='daily; vendor continuous futures, not scrap spot')
    except Exception as exc:result.update(ok=False,error=f'{type(exc).__name__}: {exc}')
    finally:http.get=original
    return result


def main():
    root=Path(__file__).resolve().parents[1]/'.test-runs'
    root.mkdir(exist_ok=True)
    if not SINA_REUSE_ALLOWED:
        previous=root/'akshare-probe.json'
        print(json.dumps({'status':'authorization_required','terms':TERMS,
                         'previous_bounded_probe':json.loads(previous.read_text(encoding='utf-8')) if previous.exists() else [],
                         'message':'No further download, production import or model training is enabled.'},ensure_ascii=True))
        return 2
    results=[]
    for symbol in SYMBOLS:
        result=probe(symbol);results.append(result)
        print(json.dumps(result,ensure_ascii=True),flush=True)
        time.sleep(1)
    (root/'akshare-probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if sum(r['ok'] for r in results)>=2 else 1


if __name__=='__main__':sys.exit(main())
