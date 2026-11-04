"""Descriptive monthly changes and aligned return correlations, not trading advice."""
from collections import defaultdict
from datetime import date,datetime,timezone
from statistics import mean
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
import market
import metal_engine as engine

router=APIRouter(prefix='/api/metals')


def shift(period,n):
    year,month=map(int,period.split('-'));total=year*12+month-1+n
    return f'{total//12:04d}-{total%12+1:02d}'


def meta(id):
    found=next((s for s in engine.all_series() if s['id']==id),None)
    if not found:raise HTTPException(400,'請先更新並選擇已取得的指標')
    return found


def window(rows,months):
    if not rows:return []
    # Chart range anchored to the latest actual observation; staleness is separate.
    cutoff=shift(rows[-1]['period'],-months+1)
    return [r for r in rows if r['period']>=cutoff]


def month_fx():
    from market_sources import get_settings
    groups=market.grouped()
    candidates=[v for v in groups.values() if v[0]['instrument']=='fx_usd']
    preferred=[v for v in candidates if v[-1]['method']=='auto:'+get_settings().provider]
    if len(preferred)==1:candidates=preferred
    else:candidates=[v for v in candidates if not v[-1]['method'].startswith('auto:')]
    if len(candidates)!=1:return {}
    monthly=defaultdict(list)
    for r in candidates[0]:monthly[r['date'][:7]].append(r)
    return {period:{'rate':mean(r['price'] for r in rows),'count':len(rows),'source':rows[-1]['source'],'last_date':rows[-1]['date']} for period,rows in monthly.items() if len(rows)>=10}


def changes(rows,n):
    if not rows:return None
    last=rows[-1];base=next((r for r in rows if r['period']==shift(last['period'],-n)),None)
    if not base:return None
    return {'base_period':base['period'],'period':last['period'],'absolute':last['value']-base['value'],'percent':(last['value']/base['value']-1)*100}


@router.get('/briefing')
def briefing(country:str='us'):
    if country not in ('us','jp','au'):raise HTTPException(400,'請選擇美國、日本或澳洲')
    items=[]
    for s in engine.all_series():
        if s['provider']!='tw_rebar' and s['id'] not in ('wb_copper','bls_copper_scrap'):continue
        rows=engine.observations(s['id'])
        if not rows:continue
        latest=rows[-1];change=changes(rows,1)
        items.append({**s,'latest':latest,'change':change,'stale':latest['period']<shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2)})
    # Match by provider, not a guessed series id, so renamed BLS series stay visible.
    if not any(x['provider']=='bls' for x in items):
        for s in engine.all_series():
            if s['provider']=='bls':
                rows=engine.observations(s['id'])
                if rows:items.append({**s,'latest':rows[-1],'change':changes(rows,1),'stale':rows[-1]['period']<shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2)})
    links={'jp':('日本官方金屬生產與供需統計','https://www.meti.go.jp/english/statistics/tyo/seidou/'),
           'au':('澳洲官方生產者價格指數','https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/producer-price-indexes-australia')}
    return {'items':items,'country':country,'reference':links.get(country),
            'message':'美國使用 BLS 廢銅價格指數；與台灣鋼筋是不同商品，只比較各自走勢，不計算價差。' if country=='us' else '尚未接入可核對規格的此國金屬價格。官方統計入口可查閱，但不是已完成的價格比較。',
            'radar':radar()}


@router.get('/trend')
def trend(series:str,months:int=120):
    if not 12<=months<=1000:raise HTTPException(400,'圖表期間須為12至1000個月')
    s=meta(series);all_rows=engine.observations(series);rows=window(all_rows,months);fx=month_fx()
    points=[{**r,'twd_value':r['value'] if s['currency']=='TWD' else r['value']*fx[r['period']]['rate'] if s['currency']=='USD' and r['period'] in fx else None,
             'fx':fx.get(r['period']) if s['currency']=='USD' else None} for r in rows]
    latest=all_rows[-1] if all_rows else None
    stale=bool(latest and latest['period']<shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2))
    change=changes(all_rows,1)
    return {'series':s,'latest':latest,'points':points,'count':len(all_rows),'stale':stale,
        'changes':{str(n):changes(all_rows,n) for n in (1,3,6,12)},
        'abnormal':bool(change and abs(change['percent'])>=engine.settings().threshold),
        'missing_fx':sum(p['twd_value'] is None for p in points) if s['currency']=='USD' else 0,
        'notes':'月度歷史、非即時行情。台幣值＝月均美元價×同月已觀察日匯率平均（至少10筆），屬近似比較，不是每日配對成交價；缺同月匯率不回填。最新值與3／6／12月變化使用原始幣別，缺確切基準月份就顯示不足。'}


class Compare(BaseModel):
    a:str
    b:str
    months:int=Field(default=120,ge=12,le=1000)
    mode:str='index'


def aligned(query):
    if query.a==query.b:raise HTTPException(400,'請選擇兩個不同指標')
    a,b=meta(query.a),meta(query.b)
    left={r['period']:r for r in engine.observations(query.a)};right={r['period']:r for r in engine.observations(query.b)}
    periods=sorted(left.keys()&right.keys())
    if periods:periods=[p for p in periods if p>=shift(periods[-1],-query.months+1)]
    return a,b,left,right,periods


@router.post('/compare')
def compare(query:Compare):
    a,b,left,right,periods=aligned(query)
    if query.mode not in ('index','native','twd'):raise HTTPException(400,'比較模式無效')
    if query.mode=='native' and (a['currency'],a['unit'])!=(b['currency'],b['unit']):raise HTTPException(400,'幣別或單位不同，請改用首月100標準化比較')
    if query.mode=='twd' and ('INDEX' in (a['currency'],b['currency']) or a['unit'].split('/')[-1]!=b['unit'].split('/')[-1]):raise HTTPException(400,'價格指數或不同物理單位不能換算成同一台幣價格；請使用標準化')
    fx=month_fx();points=[];dropped=0
    for period in periods:
        values=[left[period]['value'],right[period]['value']]
        if query.mode=='twd':
            if any(s['currency']=='USD' for s in (a,b)) and period not in fx:dropped+=1;continue
            values=[v*fx[period]['rate'] if s['currency']=='USD' else v for v,s in zip(values,(a,b))]
        points.append({'period':period,'a':values[0],'b':values[1]})
    if query.mode=='index' and points:
        first=points[0].copy()
        for p in points:p.update(a=p['a']/first['a']*100,b=p['b']/first['b']*100)
    return {'a':a,'b':b,'points':points,'missing_fx':dropped,'unit':'共同首月=100' if query.mode=='index' else ('TWD/'+a['unit'].split('/')[-1] if query.mode=='twd' else a['unit']),
            'reason':'沒有足夠的共同月份／同月匯率' if len(points)<2 else '',
            'warning':'只比較共同實際月份，不補值、不以月資料冒充日價格。不同品質／調查方法不能直接推算套利。指數不是收購價。'}


def monthly_returns(values):
    return {p:row['value']/values[shift(p,-1)]['value']-1 for p,row in values.items() if shift(p,-1) in values}


def corr(pairs):
    return market.correlation(pairs) if len(pairs)>=12 else None


@router.post('/leading')
def leading(query:Compare):
    a,b,left,right,periods=aligned(query)
    if not periods:return {'results':[],'message':'無共同月份，尚無法研究領先指標','predictive_evidence':False}
    cutoff=shift(periods[-1],-query.months+1)
    # Returns require the prior month inside the analysis window too.
    x=monthly_returns({p:r for p,r in left.items() if cutoff<=p<=periods[-1]})
    y=monthly_returns({p:r for p,r in right.items() if cutoff<=p<=periods[-1]})
    results=[]
    for lag in (0,1,2,3):
        pairs=[(x[p],y[shift(p,lag)]) for p in sorted(x) if shift(p,lag) in y]
        half=len(pairs)//2;r=corr(pairs);early=corr(pairs[:half]);late=corr(pairs[half:])
        stable=early is not None and late is not None and early*late>0 and abs(early-late)<=.3
        results.append({'lag':lag,'r':r,'samples':len(pairs),'early_r':early,'early_n':half,'late_r':late,'late_n':len(pairs)-half,'stable':stable})
    return {'a':a,'b':b,'results':results,'predictive_evidence':False,
      'message':'尚無足夠證據支持預測用途。此處以原幣／原始指數的月報酬研究 X(t) 與 Y(t+k)，不隨圖表的台幣模式改變；正k代表A領先B，沒有把未來Y當成當期輸入。資料是目前可得的修訂快照，缺少完整歷史發布版本與獨立預測驗證，相關性不代表因果。每段至少12組且非固定報酬才顯示係數；穩定僅表示前後半段同號且差距不超過0.3。'}


@router.get('/radar')
def radar():
    signals=[];limitations=[];cfg=engine.settings();series=engine.all_series();fx=month_fx()
    def emit(title,reason,rows,source,formula,limit):
        signals.append(dict(title=title,reason=reason,periods=[r['period'] for r in rows],source=source,formula=formula,limitations=limit))
    for s in series:
        rows=engine.observations(s['id'])
        if len(rows)<2:limitations.append(s['name']+'：僅'+str(len(rows))+'個月，無法計算趨勢或背離');continue
        latest=rows[-1]
        if latest['period']<shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2):
            limitations.append(s['name']+'：資料過期，僅作歷史研究，不產生近期訊號');continue
        change=changes(rows,1)
        if change and abs(change['percent'])>=cfg.threshold:
            emit(s['name']+' 異常月變動',f"月變動 {change['percent']:+.2f}%，達門檻 {cfg.threshold}%",rows[-2:],s['source_url'],'(本月值／上月值−1)×100','月均／月指數，非即時；可能修訂，需核對來源。')
        last=rows[-4:]
        if len(last)==4 and all(b['period']==shift(a['period'],1) for a,b in zip(last,last[1:])):
            differences=[b['value']-a['value'] for a,b in zip(last,last[1:])]
            if all(v>0 for v in differences) or all(v<0 for v in differences):
                emit(s['name']+' 連續'+('上漲' if differences[0]>0 else '下跌'), '連續3個月同方向變化，值得核對採購與報價風險。',last,s['source_url'],'連續3次月差同號','連續走勢不保證延續，不能直接當作買賣指令。')
        if s['id']=='wb_copper' and change:
            prev=shift(latest['period'],-1)
            if prev in fx and latest['period'] in fx:
                usd=change['percent']/100;rate=fx[latest['period']]['rate']/fx[prev]['rate']-1;twd=(1+usd)*(1+rate)-1
                emit('銅價與台幣匯率的共同影響',f'美元銅月均價 {usd*100:+.2f}%；同月平均 USD/TWD {rate*100:+.2f}%；台幣近似銅價 {twd*100:+.2f}%。'+('台幣升值抵銷部分美元漲幅。' if usd>0 and rate<0 else '匯率可能放大或抵銷商品原幣變化。'),rows[-2:],s['source_url'], '(1+美元價變動)×(1+同月平均匯率變動)−1','至少10筆同月匯率；乘積近似不是每日配對成交價，無稅運費與品質調整。')
            else:limitations.append('銅價匯率分解：缺少兩個共同月份的足量日匯率，不以今日匯率回填。')
    international=next((s for s in series if s['id']=='wb_iron_ore'),None)
    if international:
        ir={r['period']:r for r in engine.observations(international['id'])}
        for s in [s for s in series if s['provider'].startswith('tw_')]:
            local={r['period']:r for r in engine.observations(s['id'])};common=sorted(local.keys()&ir.keys())
            if len(common)<2:continue
            end=common[-1];start=shift(end,-1)
            if start not in common or end<shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2):continue
            a=local[end]['value']/local[start]['value']-1;b=ir[end]['value']/ir[start]['value']-1
            if a*b<0 and abs(a-b)*100>=cfg.threshold:
                emit(s['name']+' 與國際鐵礦砂背離',f'台灣訪價 {a*100:+.2f}%，鐵礦砂美元價 {b*100:+.2f}%。',[local[start],local[end]],s['source_url'],'同兩個月的原幣百分比方向相反且差達門檻','產品、地區、稅運費與原料不同；不是可套利價差。')
    limitations.append('豐興調價關係變化：須先有足量豐興週資料及歷史可得的外部指標版本；目前不以修訂快照宣稱預測增益。')
    return {'signals':signals,'limitations':limitations,'message':'僅為需進一步查證的研究訊號，不保證採購或出售獲利。'}


def confusion(tests):
    labels=('up','flat','down');matrix={actual:{predicted:0 for predicted in labels} for actual in labels}
    for t in tests:matrix[t['actual']][t['predicted']]+=1
    return matrix


def weekly_factor_research(weekly,baseline_tests):
    """Use only stored vintages acquired before each weekly decision origin."""
    studies=[]
    for s in engine.all_series():
        if not s['id'].startswith(('wb_','bls_')):continue
        with market.connection() as c:vintages=[dict(r) for r in c.execute('SELECT * FROM metal_observations WHERE series_id=? ORDER BY period,id',(s['id'],))]
        features=[]
        for row in weekly:
            cutoff=datetime.fromisoformat(row['published_at'])
            known={r['period']:r for r in vintages if datetime.fromisoformat(r['retrieved_at'])<=cutoff}
            periods=sorted(known)
            if len(periods)<2 or shift(periods[-1],-1)!=periods[-2] or (cutoff.date()-date.fromisoformat(periods[-1]+'-01')).days>95:
                features.append(None);continue
            features.append(market.direction(known[periods[-2]]['value'],known[periods[-1]]['value']))
        outcomes=[market.direction(a['price'],b['price']) for a,b in zip(weekly,weekly[1:])]
        tests=[]
        for i in range(26,len(outcomes)):
            if features[i] is None:continue
            cutoff=datetime.fromisoformat(weekly[i]['published_at'])
            train=[outcomes[j] for j in range(i) if features[j]==features[i] and datetime.fromisoformat(weekly[j+1]['published_at'])<=cutoff]
            if len(train)<20:continue
            p=market.probabilities(train);predicted=max(('flat','up','down'),key=lambda k:p[k])
            tests.append({'origin':weekly[i]['date'],'predicted':predicted,'actual':outcomes[i],'correct':predicted==outcomes[i]})
        matched={t['origin']:t for t in baseline_tests};n=len(tests)
        score=sum(t['correct'] for t in tests)/n if n else None
        base=sum(matched[t['origin']]['correct'] for t in tests)/n if n else None
        studies.append({'id':s['id'],'name':s['name'],'known_origins':sum(f is not None for f in features),'samples':n,
             'accuracy':score,'matched_baseline_accuracy':base,'accuracy_difference':score-base if n else None,
             'confusion_matrix':confusion(tests),'usable':False,
             'reason':'缺少當時已取得的指標版本或足夠同類訓練／測試樣本；不使用今天下載的歷史快照回填過去。' if n<20 else '僅初步成對時間序列回測；未做獨立驗證與多重比較控制，不能宣稱有可靠預測增益。'})
    return studies
