"""Runnable isolated prediction laboratory. Synthetic scores are never market forecasts.

Methods adapted from MIT steel-scrap-price-prediction: structured backward
windows + LightGBM direction classifier + expanding temporal evaluation.
No original news corpus/data/model scores are bundled. No pickle is loaded.
"""
import argparse,hashlib,json,math,re,threading,uuid
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
import scrap_analysis as sa
from lab_features import LOOKBACK,FEATURES,struct_window,direction,available_points

router=APIRouter(prefix='/api/prediction-lab')
LOCK=threading.Lock()
LABELS=['down','flat','up']
VERSION='scrap-lab-1.0'
DISCLAIMER='合成測試資料：僅驗證軟體訓練與預測流程，成績與機率不代表任何真實市場或獲利能力。'
REFERENCE='https://github.com/hyunwoomoon1990/steel-scrap-price-prediction'
REFERENCE_REV='a0b43ef2f56c469a192d2bbf0803d32ddc3f7d31'


def folder(mode):
    import app
    if mode not in ('test','real','cross'):raise ValueError('模式必須為test、real或cross')
    return app.ROOT/'research'/'prediction-lab'/mode


def real_series():
    data=sa.datasets();default=sa.catalog()['defaults']['b']
    return {k:s for k,s in data.items() if s['origin']=='fenghsin_facts' or k==default or
            (s['origin']=='metal_observations' and (s['market'].startswith('wb_') or s['market']=='bls_steel_scrap'))}


@router.get('/catalog')
def catalog():
    real=real_series()
    import cross_research
    src=cross_research.sources()
    return {'test':[{'id':key,'name':name+'（合成測試）','count':320,'frequency':'每週合成觀察'} for key,name in
                    [('demo_scrap','測試廢鋼'),('demo_rebar','測試鋼筋'),('demo_copper','測試銅')]],
            'real':[{'id':k,'name':s['name']+'／'+s['scope'],'count':len(s['points']),
                     'frequency':s['frequency'],'first':s['points'][0]['quote_date'],'last':s['points'][-1]['quote_date']} for k,s in real.items()],
            'cross':[{'id':'fh_friday','name':'豐興：星期五→後續星期一','count':len(src['target']['points']),'frequency':'1～4週'},
                     {'id':'cn_release','name':'中國官方旬度鋼價（真實國際替代研究，非豐興）','count':len(available_points(src['china'],sa.clock())),'frequency':'1～4次旬度公告'}],
            'cross_inventory':cross_research.cross_history.inventory(),
            'disclaimer':DISCLAIMER,'reference':REFERENCE,'horizon_note':'1～4個原生觀察期；測試資料為週，月指標為月，調價日資料為後續報價次數，不冒充交易日。'}


def load(mode,instrument):
    if mode=='test':
        options={'demo_scrap':(10000,0),'demo_rebar':(18000,1),'demo_copper':(7500,2)}
        if instrument not in options:raise ValueError('未知測試商品')
        base,phase=options[instrument];points=[];price=float(base)
        # Fixed deterministic fixture, not random live prices or fitted forecasts.
        for i in range(320):
            price*=1+(0 if i%15 in (7,8,9) else .007*math.sin((i+phase)/4)+.003*math.cos(i/9))
            day=(date(2020,1,6)+timedelta(weeks=i)).isoformat()
            points.append({'date':day,'quote_date':day,'price':round(price,4),'known_at':day+'T12:00:00+08:00',
                           'source':'SYNTHETIC TEST ONLY','source_url':None})
        name={'demo_scrap':'測試廢鋼','demo_rebar':'測試鋼筋','demo_copper':'測試銅'}[instrument]
        return {'id':instrument,'name':name+'（合成測試）','frequency':'weekly','points':points}
    if mode!='real':raise ValueError('未知資料模式')
    data=real_series()
    if instrument not in data:raise ValueError('未知或尚無資料的真實指標')
    return data[instrument]


def rows(series,horizon,mode):
    points=series['points'];prices=[p['price'] for p in points];result=[]
    for i in range(LOOKBACK-1,len(points)-horizon):
        p,future=points[i],points[i+horizon]
        origin=sa.endday(p['date'])
        known=max(sa.timestamp(q['known_at']) for q in points[i-LOOKBACK+1:i+1])
        if mode=='real' and known>origin:continue
        result.append({'i':i,'date':p['date'],'target_date':future['date'],
                       'target_known_at':future['known_at'],'feature_known_at':known.isoformat(),
                       'x':struct_window(prices,i),'y':direction(future['price']/p['price']-1),
                       'previous':direction(p['price']/points[i-1]['price']-1)})
    return result


def metrics(actual,predicted):
    from sklearn.metrics import accuracy_score,confusion_matrix,recall_score
    return {'samples':len(actual),'accuracy':float(accuracy_score(actual,predicted)),
            'recall':dict(zip(LABELS,recall_score(actual,predicted,labels=[0,1,2],average=None,zero_division=0).tolist())),
            'confusion_matrix':confusion_matrix(actual,predicted,labels=[0,1,2]).tolist(),'labels':LABELS}


def estimator():
    from lightgbm import LGBMClassifier
    return LGBMClassifier(n_estimators=100,learning_rate=.04,num_leaves=15,min_child_samples=10,
                          verbosity=-1,random_state=42,n_jobs=1,deterministic=True,force_col_wise=True)


def train(mode='test',instrument='demo_scrap',horizon=1):
    if horizon not in (1,2,3,4):raise ValueError('預測期間須為1～4期')
    if not LOCK.acquire(False):raise HTTPException(409,'已有模型正在訓練，請稍候')
    try:
        if mode=='cross':
            import cross_research
            return cross_research.train(instrument,horizon)
        series=load(mode,instrument);data=rows(series,horizon,mode);n=len(data)
        minimum=120
        if n<minimum:
            return {'status':'insufficient','mode':mode,'instrument':instrument,'observations':len(series['points']),
                    'eligible_samples':n,'missing_samples':minimum-n,'first_date':series['points'][0]['date'],
                    'last_date':series['points'][-1]['date'],'message':f'尚無法建立可靠模型：至少需{minimum}筆合格樣本，目前{n}筆，還缺{minimum-n}筆。今日取得的歷史快照不能冒充當年已知版本；可切換合成測試模式驗證流程。'}
        # Expanding 60/70/80% origins, 10% subsequent blocks; purge every label
        # extending to or beyond each test origin. No random split or future fill.
        folds=[];actual=[];predictions=[];previous=[];evidence=[]
        import numpy as np,lightgbm
        for ratio in (.6,.7,.8):
            start=int(n*ratio);end=min(n,start+max(1,int(n*.1)));test=data[start:end];cutoff=test[0]['date']
            training=[r for r in data[:start] if r['target_date']<cutoff and sa.timestamp(r['target_known_at'])<=sa.endday(cutoff)]
            if len(training)<60 or len({r['y'] for r in training})<3:
                return {'status':'insufficient','mode':mode,'message':'訓練期間缺少上漲、平盤或下跌類別，無法建立三分類模型。','eligible_samples':n,'missing_samples':0}
            model=estimator();model.fit(np.array([r['x'] for r in training]),np.array([r['y'] for r in training]))
            pred=model.predict(np.array([r['x'] for r in test])).astype(int).tolist();ys=[r['y'] for r in test]
            folds.append({'train_start':training[0]['date'],'train_end':training[-1]['date'],
                          'max_train_label_date':max(r['target_date'] for r in training),
                          'test_start':test[0]['date'],'test_end':test[-1]['date'],'training_samples':len(training),**metrics(ys,pred)})
            actual.extend(ys);predictions.extend(pred);previous.extend(r['previous'] for r in test)
            evidence.extend({'origin':r['date'],'target':r['target_date'],'actual':LABELS[r['y']],'predicted':LABELS[p]} for r,p in zip(test,pred))
        final=estimator();final.fit(np.array([r['x'] for r in data]),np.array([r['y'] for r in data]))
        run_id=uuid.uuid4().hex;path=folder(mode)/run_id;path.mkdir(parents=True,exist_ok=False)
        # Windows C-library fopen may reject Chinese workspace/user paths.
        (path/'model.txt').write_text(final.booster_.model_to_string(),encoding='utf-8')
        report={'status':'trained','run_id':run_id,'mode':mode,'instrument':instrument,'name':series['name'],
                'frequency':series['frequency'],'horizon':horizon,'model_version':VERSION,'engine':'LightGBM '+lightgbm.__version__,
                'reference':REFERENCE,'reference_revision':REFERENCE_REV,'reference_methods':['struct_window: backward flattened structured window','horizon direction labels','LightGBM structured baseline','expanding rolling-origin evaluation'],
                'adaptations':'三分類±0.25%平盤帶；6筆向後視窗；精確原始日期；移除向未來尋值／bfill；訓練標籤purge與發布時間檢查。',
                'created_at':datetime.now(timezone.utc).isoformat(),'training_start':data[0]['date'],'training_end':data[-1]['date'],
                'observations':len(series['points']),'training_samples':n,'feature_names':FEATURES,
                'feature_importance':dict(zip(FEATURES,final.feature_importances_.tolist())),
                'backtest':metrics(actual,predictions),'baselines':{'always_flat':metrics(actual,[1]*len(actual)),'previous_direction':metrics(actual,previous)},
                'folds':folds,'evidence':evidence,'calibrated':False,'probability_notice':'未校準模型分數，不是經驗證的市場機率。',
                'disclaimer':DISCLAIMER if mode=='test' else '真實行情研究：回測不保證未來表現；未校準機率不公開。',
                'source':series['points'][-1].get('source'),'source_url':series['points'][-1].get('source_url'),
                'last_quote_date':series['points'][-1]['date'],'latest_features':struct_window([p['price'] for p in series['points']],len(series['points'])-1),
                'dataset_hash':hashlib.sha256(json.dumps(series['points'],sort_keys=True).encode()).hexdigest()}
        (path/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report
    finally:LOCK.release()


def predict(mode,run_id):
    if not re.fullmatch('[a-f0-9]{32}',run_id):raise ValueError('模型識別碼無效')
    path=folder(mode)/run_id
    if not (path/'report.json').is_file():raise ValueError('此模式找不到模型，請先訓練')
    report=json.loads((path/'report.json').read_text(encoding='utf-8'))
    if report['mode']!=mode:raise ValueError('模型與資料模式不一致')
    from lightgbm import Booster
    import numpy as np
    raw=Booster(model_str=(path/'model.txt').read_text(encoding='utf-8')).predict(np.array([report['latest_features']]))
    classes=report.get('class_labels',[0,1,2])
    if raw.ndim==1:
        probabilities=np.array([1-float(raw[0]),float(raw[0])]);classes=report.get('class_labels',[0,1])
    else:probabilities=raw[0]
    return {'mode':mode,'run_id':run_id,'model_version':report['model_version'],'instrument':report['instrument'],'name':report['name'],
            'horizon':report['horizon'],'frequency':report['frequency'],'direction':LABELS[classes[int(np.argmax(probabilities))]],
            'probabilities':dict(zip(LABELS,probabilities.tolist())) if mode=='test' else None,
            'calibrated':False,'probability_notice':report['probability_notice'],'disclaimer':report['disclaimer'],
            'as_of_quote_date':report['last_quote_date'],'forecast_origin':report.get('forecast_origin'),'model_updated_at':report['created_at']}


class TrainInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:str=Field(pattern='^(test|real|cross)$')
    instrument:str=Field(min_length=1,max_length=400)
    horizon:int=Field(ge=1,le=4)


class PredictInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:str=Field(pattern='^(test|real|cross)$')
    run_id:str=Field(pattern='^[a-f0-9]{32}$')


@router.post('/train')
def train_api(value:TrainInput):
    try:return train(**value.model_dump())
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.post('/predict')
def predict_api(value:PredictInput):
    try:return predict(**value.model_dump())
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.post('/cross-import')
def cross_import_api():
    if not LOCK.acquire(False):raise HTTPException(409,'研究或匯入正在執行')
    try:
        import cross_history
        return cross_history.import_nbs(max_files=40)
    finally:LOCK.release()


@router.get('/cross-inventory')
def cross_inventory_api():
    import cross_history
    return cross_history.inventory()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['demo','train','predict']);parser.add_argument('--mode',choices=['test','real','cross'],default='test');parser.add_argument('--instrument',default='demo_scrap');parser.add_argument('--horizon',type=int,choices=[1,2,3,4],default=1);parser.add_argument('--run-id')
    args=parser.parse_args()
    if args.command=='predict':result=predict(args.mode,args.run_id or '')
    else:
        result=train(args.mode,args.instrument,args.horizon)
        if args.command=='demo' and result['status']=='trained':result={'training':result,'prediction':predict(args.mode,result['run_id'])}
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
