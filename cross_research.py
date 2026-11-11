"""Friday-cutoff research, with explicit dated-publication archive replay.

Never relax the existing local-acquisition PIT engine. Original official NBS
release facts form a separately labeled historical PUBLIC information replay;
today's BLS/FX snapshots remain unavailable at past cutoffs. Not a live track record.
"""
import calendar,hashlib,json,uuid
from datetime import date,datetime,timedelta,timezone
import numpy as np
import market,scrap_analysis as sa,prediction_lab as lab
from lab_features import available_points,cross_features,direction
import cross_history


def sources():
    data=sa.datasets();defaults=sa.catalog()['defaults'];result={}
    for slot,key in [('target',defaults['a']),('japan',defaults['b'])]:
        result[slot]=data.get(key,{'points':[],'frequency':'weekly'})
    for slot,market_id in [('us','bls_steel_scrap'),('fx_usd','fx_usd'),('fx_jpy','jpy_per_usd')]:
        key=next((k for k,s in data.items() if s['market']==market_id),None)
        result[slot]=data.get(key,{'points':[],'frequency':'monthly' if slot=='us' else 'daily'})
    with market.connection() as c:rows=[dict(r) for r in c.execute("SELECT * FROM global_quotes WHERE origin LIKE 'archive-nbs|%' OR origin LIKE 'nbs|%' ORDER BY quote_date,id")]
    for slot,market_id in [('china','cn_rebar'),('china_hrc','cn_hrc')]:
        points=[{'date':r['quote_date'],'quote_date':r['quote_date'],'price':r['price'],'published_at':r['published_at'],
                 'known_at':max(r['retrieved_at'],r['first_seen_at']),'first_seen_at':r['first_seen_at'],'retrieved_at':r['retrieved_at'],
                 'source_url':r['origin'].split('|')[1],'source':r['source'],'original_release':True} for r in rows if r['market']==market_id]
        result[slot]={'points':points,'frequency':'observed','currency':'CNY','unit':'t','name':'中國官方'+('螺紋鋼' if slot=='china' else '熱軋板卷')+'旬均價'}
    return result


def next_period(day):
    d=date.fromisoformat(day)
    if d.day==10:return d.replace(day=20).isoformat()
    if d.day==20:return d.replace(day=calendar.monthrange(d.year,d.month)[1]).isoformat()
    return date(d.year+int(d.month==12),1 if d.month==12 else d.month+1,10).isoformat()


def prepare(instrument,horizon):
    if instrument not in ('fh_friday','cn_release'):raise ValueError('跨市場目標無效')
    src=sources();target=src['target'] if instrument=='fh_friday' else src['china']
    # One exact price per period, never silently choose a conflicting revision.
    points=available_points(target,sa.clock());rows=[]
    external={k:v for k,v in src.items() if k not in ('target','china' if instrument=='cn_release' else 'target')}
    if not points:return src,target,rows
    # Calendar origins are fixed before looking at any target publication date.
    # An irregular/delayed release cannot move a forecast's information cutoff.
    first=date.fromisoformat(points[0]['date']);last=date.fromisoformat(points[-1]['date'])
    day=first+timedelta(days=(4-first.weekday())%7)
    origins=[]
    from weekly_research import friday_cutoff
    while day<=last:
        origins.append(friday_cutoff((day+timedelta(days=3)).isoformat()));day+=timedelta(days=7)
    for cutoff in origins:
        known=available_points(target,cutoff)
        if not known:continue
        base=known[-1]
        if instrument=='fh_friday':
            expected=(cutoff.astimezone(sa.TZ).date()+timedelta(days=3,weeks=horizon-1)).isoformat()
            future=next((p for p in points if p['date']==expected),None)
            if future is None or (cutoff.astimezone(sa.TZ).date()-date.fromisoformat(base['date'])).days>10:continue
        else:
            upcoming=[p for p in points if p['date']>base['date'] and sa.timestamp(p['published_at'])>cutoff]
            if len(upcoming)<horizon:continue
            future=upcoming[horizon-1];sequence=[base,*upcoming[:horizon]]
            if any(next_period(a['date'])!=b['date'] for a,b in zip(sequence,sequence[1:])):continue
        own=cross_features({'own':target},cutoff);other=cross_features(external,cutoff)
        target_known=future['published_at'] if future.get('original_release') else future['known_at']
        rows.append({'date':cutoff.astimezone(sa.TZ).date().isoformat(),'origin':cutoff.isoformat(),
                     'base_quote_date':base['date'],'target_date':future['date'],'target_known_at':target_known,'source_url':future.get('source_url'),
                     'y':direction(future['price']/base['price']-1,0 if instrument=='fh_friday' else .0025),
                     'previous':direction(base['price']/known[-2]['price']-1) if len(known)>1 else 1,'A':own,'B':other,'C':{**own,**other}})
    # Multiple revised source rows must not inflate evaluation samples.
    rows=list({(r['date'],r['target_date']):r for r in rows}.values());rows.sort(key=lambda r:r['date'])
    return src,target,rows


def train(instrument,horizon):
    if horizon not in (1,2,3,4):raise ValueError('期間須為1～4')
    src,target,rows=prepare(instrument,horizon);n=len(rows)
    if n<60:
        count=len(available_points(target,sa.clock()))
        return {'status':'insufficient','mode':'cross','observations':count,'eligible_samples':n,'missing_samples':max(0,60-n),
                'message':f'星期五截止研究目前只有{n}筆合格樣本（原始目標{count}筆）；至少需60筆。豐興歷史／公布版本不足，不能預測下週一。可選「中國官方旬度鋼價」執行真實國際替代研究，它不是豐興預測。','inventory':cross_history.inventory()}
    results={};models={};evidence={};names={'A':'只用目標自身歷史','B':'只用其他市場指標','C':'目標＋其他市場指標'}
    for variant in ('A','B','C'):
        columns=sorted(rows[-1][variant]);actual=[];predictions=[];previous=[];folds=[];trace=[]
        def matrix(items):return np.array([[r[variant].get(k) if r[variant].get(k) is not None else np.nan for k in columns] for r in items],dtype=float)
        for ratio in (.6,.7,.8):
            start=int(n*ratio);test=rows[start:start+max(5,int(n*.1))];origin=sa.timestamp(test[0]['origin'])
            training=[r for r in rows[:start] if sa.timestamp(r['target_known_at'])<origin]
            if len(training)<20 or len({r['y'] for r in training})<2:continue
            model=lab.estimator();model.fit(matrix(training),np.array([r['y'] for r in training]))
            pred=model.predict(matrix(test)).astype(int).tolist();ys=[r['y'] for r in test]
            actual.extend(ys);predictions.extend(pred);previous.extend(r['previous'] for r in test)
            folds.append({'train_start':training[0]['date'],'train_end':training[-1]['date'],'max_train_label_date':max(r['target_known_at'][:10] for r in training),
                          'max_train_label_published_at':max(r['target_known_at'] for r in training),
                          'test_start':test[0]['date'],'test_end':test[-1]['date'],'training_samples':len(training),**lab.metrics(ys,pred)})
            trace.extend({'origin':r['origin'],'base_quote_date':r['base_quote_date'],'target':r['target_date'],'actual':lab.LABELS[r['y']],'predicted':lab.LABELS[p],
                          'actual_published_at':r['target_known_at'],'source_url':r['source_url']} for r,p in zip(test,pred))
        if not actual:continue
        final=lab.estimator();final.fit(matrix(rows),np.array([r['y'] for r in rows]));models[variant]=final
        results[variant]={'name':names[variant],**lab.metrics(actual,predictions),'distinct_test_targets':len({r['target'] for r in trace}),
                          'folds':folds,'baselines':{'always_flat':lab.metrics(actual,[1]*len(actual)),'previous_direction':lab.metrics(actual,previous)}}
        evidence[variant]=trace
    if 'C' not in results:return {'status':'insufficient','mode':'cross','message':'真實訓練折的方向類別／歷史不足，未產生成績。'}
    now=sa.clock().astimezone(sa.TZ);days=(now.weekday()-4)%7
    latest_origin=sa.endday((now.date()-timedelta(days=days or (7 if now.weekday()==4 else 0))).isoformat())
    external={k:v for k,v in src.items() if k not in ('target','china' if instrument=='cn_release' else 'target')}
    features={**cross_features({'own':target},latest_origin),**cross_features(external,latest_origin)}
    columns=sorted(rows[-1]['C']);run=uuid.uuid4().hex;path=lab.folder('cross')/run;path.mkdir(parents=True,exist_ok=False)
    (path/'model.txt').write_text(models['C'].booster_.model_to_string(),encoding='utf-8')
    X=np.array([[r['C'].get(k) if r['C'].get(k) is not None else np.nan for k in columns] for r in rows])
    effective={slot:sum(r['B'].get(slot+'_missing')==0 and r['B'].get(slot+'_stale')==0 for r in rows) for slot in external}
    title='豐興星期五→後續星期一' if instrument=='fh_friday' else '中國官方旬度鋼材公布價（不是豐興、不是期貨）'
    leads=[]
    for slot in ('japan','us','china','china_hrc'):
        for weeks in (1,2,3,4):
            pairs=[(r['B'].get(f'{slot}_lead_{weeks}w_change'),r['y']-1) for r in rows if r['B'].get(f'{slot}_lead_{weeks}w_change') is not None]
            corr=float(np.corrcoef(np.array(pairs).T)[0,1]) if len(pairs)>=8 and np.std([x for x,y in pairs])>0 and np.std([y for x,y in pairs])>0 else None
            leads.append({'source':slot,'weeks':weeks,'samples':len(pairs),'correlation':corr,
                          'direction_agreement':sum(np.sign(x)==np.sign(y) for x,y in pairs)/len(pairs) if pairs else None,'note':'事後描述，相關不代表因果。'})
    latest_quotes=available_points(target,latest_origin)
    report={'status':'trained','mode':'cross','run_id':run,'instrument':instrument,'name':title,'horizon':horizon,'lead_analysis':leads,
            'frequency':'每週五截止；'+('後續週一' if instrument=='fh_friday' else '後續旬度公告'),
            'model_version':'cross-publication-replay-3','engine':'LightGBM '+__import__('lightgbm').__version__,
            'dataset_hash':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
            'class_labels':models['C'].classes_.tolist(),
            'training_start':rows[0]['date'],'training_end':rows[-1]['date'],'training_samples':n,'observations':len(available_points(target,sa.clock())),
            'created_at':datetime.now(timezone.utc).isoformat(),'last_quote_date':latest_quotes[-1]['date'] if latest_quotes else None,
            'forecast_origin':latest_origin.isoformat(),'cutoff_rule':'台北星期五23:59:59；晚於此刻公布的海外資料不可納入。',
            'latest_features':[features.get(k) for k in columns],'feature_names':columns,'feature_importance':dict(zip(columns,models['C'].feature_importances_.tolist())),
            'backtest':{k:v for k,v in results['C'].items() if k not in ('folds','baselines','name')},'baselines':results['C']['baselines'],
            'folds':results['C']['folds'],'comparisons':results,'evidence':evidence,'calibrated':False,
            'feature_missing_ratio':{k:float(np.isnan(X[:,i]).mean()) for i,k in enumerate(columns)},'effective_source_samples':effective,
            'adaptations':'原生頻率3/5/10/20筆動能；僅日資料稱交易日；1～4週領先特徵；缺值、年齡與過期標記；標籤公布後才可進訓練。',
            'disclaimer':'真實官方逐期公告回放，不是本系統當年持有資料的實盤績效；假設存檔公告未被回溯修改。國際替代研究不能當豐興預測，未校準不顯示機率。',
            'reliable':False,'probability_notice':'未校準且獨立回測樣本仍少，不顯示機率；僅研究推論，未達交易決策驗證。','inventory':cross_history.inventory()}
    (path/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');return report
