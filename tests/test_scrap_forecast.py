from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
import app, scrap_analysis as sa, scrap_forecast as sf
from test_scrap_analysis import prepare


def test_real_shape_insufficient_never_invents_probabilities(monkeypatch):
    with TestClient(app.app) as c:
        prepare(monkeypatch)
        r=c.get('/api/scrap-analysis/research?months=0')
        assert r.status_code==200
        data=r.json()
        assert len(data['leading'])==len(data['forecasts'])==4
        assert all(f['probabilities'] is None and not f['reliable'] for f in data['forecasts'])
        assert all(f['training_samples']==0 for f in data['forecasts'])
        assert '已取得' in data['message']
        assert c.get('/api/scrap-analysis/research?months=-1').status_code==422


def synthetic_vintages():
    """Mathematical fixtures only; never written into a price database."""
    start=date(2023,1,2);target=[];tokyo=[];price=1000;foreign=2000
    for i in range(100):
        day=start+timedelta(weeks=i)
        pattern=(0,10,0,-10,5,0,-5,0,0,10,-10)
        price+=pattern[i%len(pattern)];foreign+=(20,-10,0,10)[i%4]
        known=sa.endday(day.isoformat()).isoformat()
        target.append({'id':'TESTfh'+str(i),'date':day.isoformat(),'quote_date':day.isoformat(),
            'price':price,'known_at':known,'source':'TEST','source_url':'https://example.com/test'})
        tokyo.append({'id':'TESTjp'+str(i),'date':day.isoformat(),'quote_date':day.isoformat(),
            'price':foreign,'known_at':known,'source':'TEST','source_url':'https://example.com/test'})
    return {'target':{'market':'tw_scrap','origin':'fenghsin_facts','scope':'TEST','points':target},
            'tokyo':{'market':'jp_tokyo','origin':'global_quotes','points':tokyo}}


def test_pit_features_filter_future_revisions_and_missing_flags(monkeypatch):
    data=synthetic_vintages();original=sa.datasets
    def vintage(as_of=None):
        cutoff=sa.timestamp(as_of) if as_of else sa.clock()
        return {k:{**s,'points':[p for p in s['points'] if sa.timestamp(p['known_at'])<=cutoff]} for k,s in data.items()}
    monkeypatch.setattr(sa,'datasets',vintage)
    rows=sf.training_rows('target',data,{'international':'tokyo','jp_h2':None},35,sa.clock())
    assert len(rows[1])>52
    assert all(sa.timestamp(r['feature_known_at'])<=sa.timestamp(r['origin']) for r in rows[1])
    assert all(r['features']['jp_h2_missing']==1 and r['features']['jp_h2_change_1w'] is None for r in rows[1])
    pytest.importorskip('skforecast')
    pytest.importorskip('lightgbm')
    for horizon in (1,2,3,4):
        tests=sf.walk_forward(rows[horizon],horizon)
        assert len(tests)>=20
        assert all(sa.timestamp(t['latest_training_label_known_at'])<=sa.timestamp(t['origin']) for t in tests)
        assert all(t['actual'] in sf.LABELS and t['prediction'] in sf.LABELS for t in tests)
    # No retrospective use of a newly fetched correction.
    original=data['tokyo']['points'][-2]
    previous=data['tokyo']['points'][-3]
    late={**original,'known_at':sa.clock().isoformat(),'price':9999}
    data['tokyo']['points'].append(late)
    current=sf.features(vintage('2024-11-24T23:59:59Z'),'target','2024-11-18',sa.timestamp('2024-11-24T23:59:59Z'),{'international':'tokyo'},35)
    assert current is not None
    assert current['values']['international_change_1w']==pytest.approx(original['price']/previous['price']-1)


def test_lead_direction_is_international_t_to_target_t_plus_h():
    data=synthetic_vintages()
    result=sf.leading(data['target'],data['tokyo'],35)
    assert [r['weeks'] for r in result]==[1,2,3,4]
    for r in result:
        assert r['samples']>=80
        assert all(e['target_week']==sf.shift(e['origin_week'],r['weeks']) for e in r['evidence'])
