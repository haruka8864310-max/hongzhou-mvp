"""A single factual decision brief, no sentiment probabilities or empty markets."""
from datetime import date,timedelta
import threading
from fastapi import APIRouter,HTTPException,Query
import scrap_analysis as sa
import fenghsin,china_sources,news_sources,japan_sources,metal_engine,global_market as gm

router=APIRouter(prefix='/api/dashboard')
LOCK=threading.Lock()


def market_item(series,label,frequency,max_age,status):
    points=series['points'];last=points[-1];prev=points[-2] if len(points)>1 else None
    age=(sa.clock().astimezone(sa.TZ).date()-date.fromisoformat(last['date'])).days
    return {'id':series['id'],'label':label,'scope':series['scope'],'frequency':frequency,
        'currency':series['currency'],'unit':series['unit'],'latest':last,'count':len(points),
        'first_date':points[0]['quote_date'],'last_date':last['quote_date'],'age_days':age,'stale':age>max_age,
        'previous':prev,'change':last['price']-prev['price'] if prev else None,
        'percent':(last['price']/prev['price']-1)*100 if prev else None,'update':status}


def direction_brief(fh,markets):
    up=[];down=[];mixed=[];warnings=[]
    for m in markets:
        if m['stale']:
            warnings.append(f'{m["label"]}報價距今{m["age_days"]}日；不納入本週偏多／偏空結論。')
            continue
        if m['change'] is None or not m['previous']:
            warnings.append(m['label']+'只有一筆有效價格，不能判斷變化。');continue
        if not m['change']:continue
        polarity='up' if m['change']>0 else 'down'
        factor={'title':m['label']+('上升' if polarity=='up' else '下降'),
                'reason':f'相較{m["previous"]["quote_date"]}變動{m["percent"]:+.2f}%。'+
                    ('下游鋼材強弱可能影響原料需求；中國旬均成品價不是台灣廢鋼價。' if m['label'].startswith('中國') else
                     '美國國內月指數反映較慢的產業背景，不能當HMS成交價或下週牌價。' if m['label'].startswith('美國') else
                     '日本鋼廠採購變化值得留意，但原品級、交貨與台灣不同，不能視為價差套利。'),
                'date':m['last_date'],'source':m['latest']['source'],'url':m['latest']['source_url'],'strength':'間接線索，非因果預測'}
        (up if polarity=='up' else down).append(factor)
    # Verified numeric change facts, not article sentiment or invented base prices.
    rebar=fenghsin.history('rebar',3)['points']
    if len(rebar)>=2 and (sa.clock().astimezone(sa.TZ).date()-date.fromisoformat(rebar[-1]['date'])).days<=8:
        a,b=rebar[-2:];change=b['price']-a['price']
        if change:
            factor={'title':'豐興鋼筋基價'+('上調' if change>0 else '下調'),
                    'reason':f'相較{a["date"]}{change:+g}元／公噸。這是成品鋼筋，不是廢鋼收購價；只能作下游壓力線索。',
                    'date':b['date'],'source':b['source'],'url':b['source_url'],'strength':'間接線索'}
            (up if change>0 else down).append(factor)
            ps=fh['points']
            if len(ps)>=2 and ps[-1]['price']==ps[-2]['price']:
                mixed.append({'title':'成品已調整，廢鋼仍平盤','reason':'鋼筋變動尚未同向反映在最新廢鋼收購價，不能直接推定下週跟進。','date':ps[-1]['date'],'source':ps[-1]['source'],'url':ps[-1]['source_url']})
    if up and down:
        mixed.append({'title':'海外訊號方向不一致','reason':'目前同時有上升與下降因素，且頻率、地區與品級不同；不以因素數量換算漲跌機率。','date':None,'source':None,'url':None})
    warnings.append('豐興價格為供應商公開彙整，品級／稅別未詳且尚未獨立驗證。')
    warnings.append('土耳其HMS CFR及美國CFR Taiwan尚缺免費且授權適用的同規格歷史，未納入主訊號；BLS不是其替代成交價。')
    title='目前偏弱線索較多，尚不能確定豐興下週調降' if down and not up else '目前偏強線索較多，尚不能確定豐興下週調漲' if up and not down else '目前訊號互有矛盾，應先核對報價與需求' if up and down else '目前沒有足夠新鮮訊號可判定豐興下週方向'
    return {'headline':title,'up':up,'down':down,'mixed':mixed,'warnings':warnings,
            'note':'這是可追溯的市場因素整理，不是已驗證模型預測；不提供沒有回測支持的百分比。'}


@router.get('')
def overview(months:int=Query(6,ge=0,le=120)):
    data=sa.datasets();defaults=sa.catalog()['defaults'];items=[];missing=[]
    choices=[(defaults['b'],'日本東京製鐵岡山／二級','調價日報價',14,japan_sources.state()),
        (next((k for k,s in data.items() if s['market']=='bls_steel_scrap'),''),'美國廢鋼PPI','月度指數',95,next((s for s in metal_engine.status() if s['provider']=='bls'),{})),
        (next((k for k,s in data.items() if s['market']=='cn_rebar' and any(p['source_url'].startswith('https://www.stats.gov.cn/') for p in s['points'])),''),'中國螺紋鋼 HRB400E Φ20mm','旬均批發／銷售價',25,china_sources.state())]
    for key,label,freq,age,status in choices:
        if key in data:items.append(market_item(data[key],label,freq,age,status))
        else:missing.append({'market':label,'reason':status.get('error') or '尚未成功取得此固定口徑資料，請更新並展開來源結果查看；未產生空白價格卡片。','status':status})
    fh=fenghsin.history('scrap',months)
    all_fh=fenghsin.history('scrap',0)
    fh['latest']=all_fh['points'][-1] if all_fh['points'] else None
    fh['total_count']=len(all_fh['points'])
    return {'as_of':sa.clock().isoformat(),'fenghsin':fh,'markets':items,'missing':missing,
            'direction':direction_brief(all_fh,items),'news':news_sources.listing(),
            'sources':{'china':china_sources.state(),'news':news_sources.state(),'japan':japan_sources.state()}}


@router.post('/update')
def update():
    if not LOCK.acquire(False):raise HTTPException(409,'市場與新聞正在更新，請稍候')
    results=[]
    try:
        jobs=[('中國官方旬度資料',china_sources.HOME,lambda:china_sources.sync(True)),
              ('中文公告索引',news_sources.HOME,lambda:news_sources.sync(True)),
              ('豐興週盤',fenghsin.SOURCES['juyi'][1],lambda:fenghsin.sync(True)),
              ('日本官方採購',japan_sources.HOME,lambda:japan_sources.sync(True)),
              ('美國BLS月度指數',metal_engine.PROVIDERS['bls']['url'],lambda:metal_engine.sync(True,providers=['bls']))]
        for name,url,job in jobs:
            try:
                value=job();results.append({'name':name,'url':url,**value})
            except Exception as exc:results.append({'name':name,'url':url,'ok':False,'message':f'{type(exc).__name__}：{exc}'})
        gm.capture_existing()
        return {'ok':all(r.get('ok',False) for r in results),'results':results,'message':'已完成各來源檢查。下載成功不代表有新報價；失敗來源保留既有資料。'}
    finally:LOCK.release()
