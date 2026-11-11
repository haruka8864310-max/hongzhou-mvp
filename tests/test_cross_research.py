from datetime import datetime,timedelta,date
from fastapi.testclient import TestClient
import app,cross_research as cr,prediction_lab as lab
from lab_features import available_points,cross_features
from weekly_research import friday_cutoff


def test_friday_cutoff_and_publication_leakage():
    cutoff=friday_cutoff('2026-11-09')
    assert cutoff.isoformat().startswith('2026-11-06T15:59')
    p={'date':'2026-11-06','price':100,'known_at':'2026-11-11T00:00:00+00:00','published_at':'2026-11-07T00:00:00+00:00','original_release':True}
    assert available_points({'points':[p]},cutoff)==[]
    p['published_at']='2026-11-06T01:30:00+00:00'
    assert len(available_points({'points':[p]},cutoff))==1
    p['original_release']=False
    assert available_points({'points':[p]},cutoff)==[]


def test_native_frequency_is_not_trading_days():
    points=[{'date':(date(2024,1,1)+timedelta(days=i*10)).isoformat(),'price':100+i,
             'known_at':'2023-01-01T00:00:00+00:00'} for i in range(25)]
    values=cross_features({'test':{'frequency':'observed','points':points}},friday_cutoff('2024-09-09'))
    assert values['test_return_3_trading_days'] is None
    assert values['test_return_3_observations'] is not None
    assert values['comparable_overseas_scrap_spread'] is None


def test_cross_mode_keeps_test_and_real_separate():
    with TestClient(app.app) as client:
        assert len(client.get('/api/prediction-lab/catalog').json()['cross'])==2
        value=client.post('/api/prediction-lab/train',json={'mode':'cross','instrument':'fh_friday','horizon':1}).json()
        assert value['status']=='insufficient' and value['eligible_samples']==0
        assert client.get('/').status_code==200
        assert client.get('/prediction-lab').status_code==200


def test_dated_archive_rows_and_fold_labels_cannot_cross_origin(monkeypatch):
    # Explicit isolated synthetic fixture ONLY to verify temporal invariants;
    # never included in the real-source acceptance numbers or model performance.
    import math
    pts=[]
    d=date(2020,1,10)
    for i in range(150):
        pts.append({'date':d.isoformat(),'price':100+5*math.sin(i/3),'known_at':'2026-11-11T00:00:00+00:00',
                    'published_at':(d+timedelta(days=3)).isoformat()+'T01:30:00+00:00','original_release':True,'source_url':'https://example.com/TEST'})
        d=date.fromisoformat(cr.next_period(d.isoformat()))
    src={slot:{'points':pts if slot in ('china','china_hrc') else [],'frequency':'observed'} for slot in ('target','japan','us','fx_usd','fx_jpy','china','china_hrc')}
    monkeypatch.setattr(cr,'sources',lambda:src)
    with TestClient(app.app):
        # Missing periods cannot become a fake one-period target.
        removed=src['china']['points'].pop(30)
        _,_,audited=cr.prepare('cn_release',1)
        assert all(cr.next_period(r['base_quote_date'])==r['target_date'] for r in audited)
        src['china']['points'].insert(30,removed)
        _,_,before=cr.prepare('cn_release',1)
        origin=before[30]['origin']
        future=next(p for p in pts if p['published_at']>origin)
        original=future['price'];future['price']*=10
        _,_,after=cr.prepare('cn_release',1)
        same=next(r for r in after if r['origin']==origin)
        assert same['A']==before[30]['A'] and same['B']==before[30]['B']
        future['price']=original
        assert all(date.fromisoformat(r['date']).weekday()==4 for r in before)
        report=lab.train('cross','cn_release',1)
        assert report['status']=='trained' and set(report['comparisons'])=={'A','B','C'}
        for m in report['comparisons'].values():
            for fold in m['folds']:
                assert __import__('scrap_analysis').timestamp(fold['max_train_label_published_at'])<__import__('scrap_analysis').endday(fold['test_start'])
        assert lab.predict('cross',report['run_id'])['probabilities'] is None
