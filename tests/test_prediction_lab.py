import json
import numpy as np
from fastapi.testclient import TestClient
import app,prediction_lab as lab
from lab_features import struct_window


def test_backward_features_do_not_use_future():
    a=[100,101,102,100,99,101,200]
    b=[*a[:6],99999]
    assert struct_window(a,5)==struct_window(b,5)


def test_train_predict_and_saved_model_are_separate_from_market_database():
    with TestClient(app.app) as c:
        before=app.DB.read_bytes()
        r=c.post('/api/prediction-lab/train',json={'mode':'test','instrument':'demo_scrap','horizon':2})
        assert r.status_code==200,r.text
        model=r.json();assert model['status']=='trained' and model['backtest']['samples']>0
        assert model['engine'].startswith('LightGBM') and model['training_samples']==313
        for f in model['folds']:assert f['max_train_label_date']<f['test_start']
        p=c.post('/api/prediction-lab/predict',json={'mode':'test','run_id':model['run_id']}).json()
        assert p['direction'] in lab.LABELS and abs(sum(p['probabilities'].values())-1)<1e-8
        assert not p['calibrated'] and '合成測試' in p['disclaimer']
        assert app.DB.read_bytes()==before
        assert (lab.folder('test')/model['run_id']/'model.txt').is_file()
        assert c.post('/api/prediction-lab/predict',json={'mode':'real','run_id':model['run_id']}).status_code==422
        # A newly loaded Booster produces identical probabilities after restart.
        assert lab.predict('test',model['run_id'])['probabilities']==p['probabilities']


def test_real_snapshot_refuses_historical_leakage_but_demo_still_runs(monkeypatch):
    s=lab.load('test','demo_scrap');s['id']='real-test'
    for p in s['points']:p['known_at']='2026-11-11T12:00:00+00:00'
    monkeypatch.setattr(lab,'real_series',lambda:{'real-test':s})
    r=lab.train('real','real-test',1)
    assert r['status']=='insufficient' and r['eligible_samples']==0 and r['missing_samples']==120
    assert lab.train('test','demo_rebar',4)['status']=='trained'


def test_lab_page_inputs_and_mode_validation():
    with TestClient(app.app) as c:
        assert c.get('/prediction-lab').status_code==200
        catalog=c.get('/api/prediction-lab/catalog').json()
        assert len(catalog['test'])==3
        assert c.post('/api/prediction-lab/train',json={'mode':'test','instrument':'fake','horizon':1}).status_code==422
        assert c.post('/api/prediction-lab/train',json={'mode':'test','instrument':'demo_scrap','horizon':5}).status_code==422
        assert c.post('/api/prediction-lab/predict',json={'mode':'test','run_id':'../escape'}).status_code==422
