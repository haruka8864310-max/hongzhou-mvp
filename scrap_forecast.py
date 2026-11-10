"""Strict, expanding-origin 1-4 week direction research. No random forecasts.

skforecast supplies temporal folds; LightGBM fits direct three-class models.
Historical features require actual acquired vintages at each origin. Snapshot
correlations are explicitly separate and cannot unlock a forecast.
"""
import math
from collections import Counter
from datetime import date, timedelta
import importlib.metadata
import threading
from fastapi import HTTPException
import market
import scrap_analysis as sa

LOCK = threading.Lock()
LABELS = ('down', 'flat', 'up')
MIN_TRAIN = 52
MIN_TEST = 20
MIN_CLASS = 5


def week_end(week):
    return (date.fromisoformat(week)+timedelta(days=6)).isoformat()


def shift(week, n):
    return (date.fromisoformat(week)+timedelta(weeks=n)).isoformat()


def direction(a, b):
    return 'up' if b > a else 'down' if b < a else 'flat'


def correlation(pairs):
    return market.correlation(pairs) if len(pairs) >= 8 else None


def leading(target, international, max_age):
    """Descriptive quote-date relation, never claimed as PIT backtest."""
    weeks=sa.week_rows(target)
    result=[]
    for horizon in (1,2,3,4):
        pairs=[]
        evidence=[]
        for week, base in weeks.items():
            future=weeks.get(shift(week,horizon))
            day=week_end(week)
            previous=(date.fromisoformat(day)-timedelta(days=7)).isoformat()
            a=sa.latest(international['points'],previous,max_age)
            b=sa.latest(international['points'],day,max_age)
            if not (future and a and b):continue
            x=b['price']/a['price']-1
            y=future['price']/base['price']-1
            pairs.append((x,y))
            evidence.append({'origin_week':week,'target_week':shift(week,horizon),
                'international_ids':[a['id'],b['id']],'target_ids':[base['id'],future['id']],
                'international_age_days':b['age_days']})
        half=len(pairs)//2
        result.append({'weeks':horizon,'samples':len(pairs),'correlation':correlation(pairs),
            'direction_agreement':sum(direction(0,x)==direction(0,y) for x,y in pairs)/len(pairs) if pairs else None,
            'early_correlation':correlation(pairs[:half]),'late_correlation':correlation(pairs[half:]),
            'nonflat_pairs':sum(x!=0 and y!=0 for x,y in pairs),
            'evidence':evidence,
            'reason':'目前版本的事後描述；同期持平也計入一致率。非當時可得回測，相關不代表因果。'+(' 至少8組且變化不能固定，才顯示係數。' if correlation(pairs) is None else '')})
    return result


def feature_sources(data, chosen):
    ids={'international':chosen}
    for name,markets in [('jp_h2',('jp_h2',)),('jp_fob',('jp_fob',)),
                         ('us_scrap',('us_cfr_tw','us_hms','us_shredded')),
                         ('us_index',('us_index','bls_steel_scrap')),
                         ('cn_rebar',('cn_rebar',)),('cn_hrc',('cn_hrc',)),('cn_iron',('cn_iron',)),
                         ('fx_usd',('fx_usd',)),('fx_jpy',('fx_jpy',))]:
        ids[name]=next((k for market_id in markets for k,s in data.items() if s['market']==market_id),None)
    return ids


def features(data, target_id, week, origin, sources, max_age):
    target=data.get(target_id)
    weeks=sa.week_rows(target) if target else {}
    base=weeks.get(week)
    if not base:return None
    values={}
    known=[]
    for lag in (1,2,4):
        previous=weeks.get(shift(week,-lag))
        if not previous:return None
        values[f'fh_change_{lag}w']=base['price']/previous['price']-1
        known.extend([base['known_at'],previous['known_at']])
    for name,key in sources.items():
        series=data.get(key)
        points=series['points'] if series else []
        current=sa.latest(points,origin.astimezone(sa.TZ).date().isoformat(),max_age,origin)
        values[name+'_missing']=int(current is None)
        values[name+'_age_days']=current['age_days'] if current else None
        stale_limit={'daily':7,'weekly':10,'monthly':95,'annual':370,'observed':35}.get(series.get('frequency') if series else None,35)
        values[name+'_stale']=int(current is None or current['age_days']>stale_limit)
        for lag in (1,2,4):
            prior=sa.latest(points,(origin.astimezone(sa.TZ).date()-timedelta(weeks=lag)).isoformat(),max_age,origin)
            values[f'{name}_change_{lag}w']=current['price']/prior['price']-1 if current and prior else None
            if current and prior:known.extend([current['known_at'],prior['known_at']])
        for lag in (1,2,3,4):
            end=(origin.astimezone(sa.TZ).date()-timedelta(weeks=lag)).isoformat()
            start=(date.fromisoformat(end)-timedelta(days=7)).isoformat()
            a,b=sa.latest(points,start,max_age,origin),sa.latest(points,end,max_age,origin)
            values[f'{name}_return_lag_{lag}w']=b['price']/a['price']-1 if a and b else None
            if a and b:known.extend([a['known_at'],b['known_at']])
    if known and max(sa.timestamp(k) for k in known)>origin:
        raise ValueError('禁止使用預測時點之後取得的特徵')
    return {'values':values,'base':base,'feature_known_at':max(known) if known else None}


def training_rows(target_id, data, sources, max_age, as_of):
    target=data[target_id]
    observed=sa.week_rows(target)
    rows={h:[] for h in (1,2,3,4)}
    # Bound research CPU to the latest 3 years of genuine target observations.
    for week in sorted(observed)[-156:]:
        origin=sa.endday(week_end(week))
        if origin>as_of:continue
        vintage=sa.datasets(origin.isoformat())
        feature=features(vintage,target_id,week,origin,sources,max_age)
        if not feature:continue
        for h in rows:
            outcome=observed.get(shift(week,h))
            if not outcome:continue
            rows[h].append({'week':week,'origin':origin.isoformat(),'features':feature['values'],
                'feature_known_at':feature['feature_known_at'],'outcome_week':shift(week,h),
                'outcome_known_at':outcome['known_at'],
                'label':direction(feature['base']['price'],outcome['price'])})
    return rows


def engine_status():
    packages={}
    for name in ('skforecast','lightgbm'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]=None
    return {'packages':packages,'available':all(packages.values()),'min_train':MIN_TRAIN,'min_test':MIN_TEST}


def paired_p(tests):
    wins=sum(t['prediction']==t['actual'] and t['actual']!='flat' for t in tests)
    losses=sum(t['prediction']!=t['actual'] and t['actual']=='flat' for t in tests)
    n=wins+losses
    return min(1.0,sum(math.comb(n,k) for k in range(wins,n+1))/2**n) if n else 1.0


def classifier():
    from lightgbm import LGBMClassifier
    return LGBMClassifier(n_estimators=40,max_depth=3,num_leaves=7,learning_rate=.05,
        min_child_samples=5,random_state=2015,n_jobs=1,verbosity=-1,deterministic=True,force_col_wise=True)


def walk_forward(rows,horizon):
    tests=[]
    if len(rows)<=MIN_TRAIN+horizon:return tests
    import pandas as pd
    from skforecast.model_selection import TimeSeriesFold
    cv=TimeSeriesFold(steps=1,initial_train_size=MIN_TRAIN,window_size=1,refit=True,
        fixed_train_size=False,gap=horizon,allow_incomplete_fold=False,verbose=False)
    folds=cv.split(pd.Series(range(len(rows))))
    for fold in folds[-100:]:
        train_start,train_end=fold[1]
        test_start,test_end=fold[4]
        test=rows[test_start]
        train=[r for r in rows[train_start:train_end] if sa.timestamp(r['outcome_known_at'])<=sa.timestamp(test['origin'])]
        counts=Counter(r['label'] for r in train)
        if len(train)<MIN_TRAIN or any(counts[label]<MIN_CLASS for label in LABELS):continue
        frame=pd.DataFrame([r['features'] for r in train],dtype=float)
        test_frame=pd.DataFrame([test['features']],columns=frame.columns,dtype=float)
        model=classifier().fit(frame,[r['label'] for r in train])
        prediction=str(model.predict(test_frame)[0])
        target_columns=[c for c in frame if c.startswith('fh_')]
        target_only=classifier().fit(frame[target_columns],[r['label'] for r in train])
        target_prediction=str(target_only.predict(test_frame[target_columns])[0])
        majority=max(('flat','up','down'),key=lambda label:counts[label])
        tests.append({'origin':test['origin'],'outcome_week':test['outcome_week'],
            'actual':test['label'],'prediction':prediction,'majority':majority,'target_only':target_prediction,
            'train_samples':len(train),'latest_training_label_known_at':max(r['outcome_known_at'] for r in train),
            'feature_known_at':test['feature_known_at']})
    return tests


def forecast(target_id, data, sources, rows, horizon, max_age, now):
    current_target=data[target_id]
    current_week=max(sa.week_rows(current_target))
    current=features(data,target_id,current_week,now,sources,max_age)
    available=[r for r in rows if sa.timestamp(r['outcome_known_at'])<=now]
    result={'weeks':horizon,'reliable':False,'probabilities':None,'training_samples':len(available),
            'target_week':shift(current_week,horizon),'origin_as_of':now.isoformat(),
            'backtest_samples':0,'accuracy':None,'flat_accuracy':None,'majority_accuracy':None,
            'target_only_accuracy':None,'factors':[],'model_updated_at':None,'evaluated_at':now.isoformat(),
            'confidence':'尚無可靠預測','quality':{},'reason':'','tests':[],'confusion_matrix':None}
    result['quality']={'target_observations':len(current_target['points']),
        'known_feature_origins':len(rows),'missing_sources':[k for k,v in sources.items() if not v or v not in data],
        'target_scope':current_target['scope'],'snapshot_is_not_historical_vintage':True}
    if current:
        result['quality']['missing_sources']=[k for k in sources if current['values'].get(k+'_missing')]
        result['quality']['stale_sources']=[k for k in sources if current['values'].get(k+'_stale')]
    if not current or len(available)<MIN_TRAIN:
        result['reason']=f'只有{len(current_target["points"])}個目標報價日期，且具當時可得特徵的訓練樣本為{len(available)}；至少需{MIN_TRAIN}筆訓練與{MIN_TEST}筆時間順序回測。今日下載的歷史不能回填為過去已知，未產生機率。'
        return result
    engine=engine_status()
    if not engine['available']:
        result['reason']='缺少可選研究依賴skforecast／LightGBM；走勢比較仍可用，未用固定比例替代模型。'
        return result
    tests=walk_forward(rows,horizon)
    n=len(tests)
    result.update(tests=tests,backtest_samples=n,
        accuracy=sum(t['prediction']==t['actual'] for t in tests)/n if n else None,
        flat_accuracy=sum(t['actual']=='flat' for t in tests)/n if n else None,
        majority_accuracy=sum(t['majority']==t['actual'] for t in tests)/n if n else None,
        target_only_accuracy=sum(t['target_only']==t['actual'] for t in tests)/n if n else None,
        confusion_matrix={actual:{pred:sum(t['actual']==actual and t['prediction']==pred for t in tests) for pred in LABELS} for actual in LABELS},
        paired_p=paired_p(tests))
    counts=Counter(r['label'] for r in available)
    if n<MIN_TEST or any(counts[label]<MIN_CLASS for label in LABELS):
        result['reason']=f'具時間順序驗證的回測只有{n}筆，或上漲／平盤／下跌類別不足各{MIN_CLASS}筆；不輸出機率。'
        return result
    # Paired significance uses a four-horizon Bonferroni threshold; descriptive
    # correlations cannot meet this gate. No tuned-on-test hyperparameters.
    if result['accuracy']<=max(result['flat_accuracy'],result['majority_accuracy'],result['target_only_accuracy'])+.05 or result['paired_p']>=.0125:
        result['reason']='時間順序回測尚未同時超過平盤、多數類別與僅豐興特徵基準至少5個百分點，或成對檢定不足（四期間門檻0.0125）；尚無可靠預測。'
        return result
    if current['values'].get('international_missing') or current['values'].get('international_stale') or (now.astimezone(sa.TZ).date()-date.fromisoformat(current['base']['date'])).days>10:
        result['reason']='最新豐興或國際特徵缺值／過期，不發布機率。歷史回測保留。'
        return result
    import pandas as pd
    frame=pd.DataFrame([r['features'] for r in available],dtype=float)
    model=classifier().fit(frame,[r['label'] for r in available])
    current_frame=pd.DataFrame([current['values']],columns=frame.columns,dtype=float)
    probability=model.predict_proba(current_frame)[0]
    result.update(probabilities={str(label):float(p) for label,p in zip(model.classes_,probability)},
        model_updated_at=now.isoformat(),reliable=True,confidence='研究估計；未做獨立機率校準，不是確定消息',
        reason='通過目前設定的樣本與成對回測門檻，仍有小樣本、版本修訂及結構改變風險；不是交易保證。')
    result['factors']=[{'name':name,'importance':int(value),'value':current['values'][name]} for name,value in
        sorted(zip(frame.columns,model.feature_importances_),key=lambda x:x[1],reverse=True)[:8] if value]
    return result


def research(a='',b='',months=12,max_age_days=35,as_of=None):
    if not LOCK.acquire(False):raise HTTPException(409,'研究正在執行，請稍候')
    try:
        sa.Preferences(months=months,max_age_days=max_age_days)
        now=sa.timestamp(as_of) if as_of else sa.clock()
        data=sa.datasets(now.isoformat())
        defaults=sa.catalog(now.isoformat())['defaults']
        a,b=a or defaults['a'],b or defaults['b']
        if a not in data or b not in data:raise HTTPException(422,'缺少豐興或所選國際指標')
        target=data[a]
        if target['market']!='tw_scrap' or target['origin']!='fenghsin_facts':
            raise HTTPException(422,'預測目標固定為豐興廢鋼收購價，請將A選為豐興廢鋼')
        visible_target={**target,'points':[p for p in target['points'] if not months or p['date']>=(now.astimezone(sa.TZ).date()-timedelta(days=round(months*30.4375))).isoformat()]}
        if not visible_target['points']:raise HTTPException(422,'所選期間無豐興價格')
        sources=feature_sources(data,b)
        training=training_rows(a,data,sources,max_age_days,now)
        forecasts=[forecast(a,data,sources,training[h],h,max_age_days,now) for h in (1,2,3,4)]
        return {'leading':leading(visible_target,data[b],max_age_days),'forecasts':forecasts,'engine':engine_status(),
            'as_of':now.isoformat(),'feature_sources':sources,
            'message':'領先表依所選期間作事後歷史快照描述；模型另取最近最多156個目標週，且只用各預測時點已取得版本，兩者樣本數不同。未以相關係數代替預測勝率；1～4週代表相對本期價的累計方向，平盤指完全相同價格。'}
    finally:LOCK.release()
