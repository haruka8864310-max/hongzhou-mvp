"""Executable real-data research; engine verification is explicitly non-market data."""
import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))


def fit_candidate(rows,horizon,now,folder):
    import scrap_forecast as sf
    import scrap_analysis as sa
    valid=[r for r in rows if sa.timestamp(r['outcome_known_at'])<=now]
    if any(sa.timestamp(r['feature_known_at'])>sa.timestamp(r['origin']) or sa.timestamp(r['origin'])>now for r in valid):
        raise ValueError('禁止使用預測時點之後的特徵訓練')
    counts=Counter(r['label'] for r in valid)
    if len(valid)<sf.MIN_TRAIN or any(counts[k]<sf.MIN_CLASS for k in sf.LABELS):
        return {'weeks':horizon,'trained':False,'training_samples':len(valid),
                'missing_training_samples':max(0,sf.MIN_TRAIN-len(valid)),
                'missing_class_samples':{k:max(0,sf.MIN_CLASS-counts[k]) for k in sf.LABELS},
                'reason':'缺少足量、當時可得且三類皆有樣本的訓練資料；未建立假模型。'}
    import pandas as pd
    frame=pd.DataFrame([r['features'] for r in valid],dtype=float)
    model=sf.classifier().fit(frame,[r['label'] for r in valid])
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'candidate-h{horizon}.txt'
    if path.exists():raise ValueError('拒絕覆蓋既有模型')
    # LightGBM's native Windows file API can reject Chinese absolute paths.
    # Serialize via Python's Unicode-safe exclusive file writer instead.
    with path.open('x',encoding='utf-8') as stream:
        stream.write(model.booster_.model_to_string())
    manifest={'weeks':horizon,'trained':True,'training_samples':len(valid),'classes':list(model.classes_),
              'features':list(frame.columns),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'model_file':str(path.resolve()),'trained_as_of':now.isoformat(),
              'latest_training_label_known_at':max(r['outcome_known_at'] for r in valid),
              'engine':sf.engine_status(),'status':'candidate_not_approved_for_live_predictions'}
    path.with_suffix('.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def verify_engine(folder):
    """Mathematical classification fixture, not invented commodity price history."""
    import scrap_analysis as sa
    import scrap_forecast as sf
    output=[]
    now=sa.clock()
    for h in (1,2,3,4):
        rows=[]
        for i in range(108):
            origin=sa.endday((date(2020,1,5)+timedelta(weeks=i)).isoformat())
            rows.append({'origin':origin.isoformat(),'week':str(i),'outcome_week':str(i+h),
                'feature_known_at':(origin-timedelta(days=1)).isoformat(),
                'outcome_known_at':(origin+timedelta(weeks=h)).isoformat(),
                'features':{'fh_change_1w':float(i%3),'international_change_1w':float(i%7)},
                'label':sf.LABELS[(i+h)%3],'previous_direction':sf.LABELS[(i-1)%3]})
        trained=fit_candidate(rows,h,now,folder)
        tests=sf.walk_forward(rows,h)
        assert trained['trained'] and len(tests)>=sf.MIN_TEST
        assert all(sa.timestamp(t['latest_training_label_known_at'])<=sa.timestamp(t['origin']) for t in tests)
        output.append({'weeks':h,'trained':True,'training_samples':trained['training_samples'],
                       'rolling_tests':len(tests),'metrics':sf.classification_metrics(tests)})
    return {'purpose':'純合成數學資料驗證套件呼叫，不是行情、不是豐興回測成績，不供正式預測',
            'market_data':False,'engine':sf.engine_status(),'horizons':output}


def main(argv=None):
    parser=argparse.ArgumentParser(description='弘洲真實資料訓練／時間順序回測（唯讀正式庫）')
    parser.add_argument('command',choices=('doctor','weekly','train','backtest','verify-engine'))
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--as-of')
    args=parser.parse_args(argv)
    if args.data_dir:os.environ['HONGZHOU_DATA_DIR']=str(args.data_dir.resolve())
    import app
    import scrap_forecast as sf
    import scrap_analysis as sa
    import weekly_research as wr
    base=args.output_dir or app.ROOT/'research'
    run_folder=base/('engine-verification' if args.command=='verify-engine' else 'runs')/uuid.uuid4().hex
    try:
        if args.command=='doctor':
            # Real imports, not just metadata presence.
            from skforecast.model_selection import TimeSeriesFold
            from lightgbm import LGBMClassifier
            result={'python':sys.version,'engine':sf.engine_status(),
                    'imports':{'TimeSeriesFold':TimeSeriesFold.__module__,'LGBMClassifier':LGBMClassifier.__module__},
                    'source_database':str(app.DB),'exists':app.DB.is_file()}
        elif args.command=='verify-engine':
            result=verify_engine(run_folder)
        else:
            if not app.DB.is_file():raise ValueError('資料庫不存在；未建立或初始化空白正式資料庫')
            context=wr.prepare(args.as_of)
            saved=wr.persist(context,base)
            training=sf.training_rows(context['target_id'],context['data'],context['sources'],35,
                                      context['now'],context['vintages'])
            result={'command':args.command,'source_database':str(app.DB),'weekly':saved,
                    'data':context['summary'],'engine':sf.engine_status()}
            if args.command=='train':
                result['models']=[fit_candidate(training[h],h,context['now'],run_folder) for h in (1,2,3,4)]
            elif args.command=='backtest':
                result['forecasts']=[sf.forecast(context['target_id'],context['data'],context['sources'],
                                    training[h],h,35,context['now']) for h in (1,2,3,4)]
                result['signals']=sf.leading(context['data'][context['target_id']],context['data'][context['sources']['international']],35) if context['sources']['international'] else []
        run_folder.mkdir(parents=True,exist_ok=True)
        report=run_folder/'report.json'
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps({'report':str(report.resolve()),**result},ensure_ascii=True,indent=2,allow_nan=False))
        if args.command=='train' and not all(m['trained'] for m in result['models']):return 2
        if args.command=='backtest' and not any(f['backtest_samples'] for f in result['forecasts']):return 2
        return 0
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__,'reason':str(exc)},ensure_ascii=True),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
