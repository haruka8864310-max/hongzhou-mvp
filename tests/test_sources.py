"""Deterministic synthetic provider fixtures; no network in regression tests."""
from datetime import datetime,timedelta,timezone
from urllib.error import URLError
import pytest
from fastapi.testclient import TestClient
import app
import market
import market_sources as sources


@pytest.fixture
def client():
    with TestClient(app.app) as c:
        yield c


def payload(days=40, stale=0):
    today=datetime.now(sources.TAIPEI).date()-timedelta(days=stale)
    rows=[[(today-timedelta(days=i)).strftime('%Y%m%d'),str(32+i*.01),'160','8'] for i in range(days)]
    return {'meta':{'last_updated':today.isoformat()},'data':{'structure':{'Table1':[{'data':'NTD/USD'},{'data':'JPY/USD'},{'data':'CNY/USD'}]},'dataSets':rows}}


def test_settings_validation_and_restart(client):
    original=client.get('/api/market/settings').json()
    saved={**original,'tracked_markets':['fx_usd','fx_jpy'],'compare_markets':['fx_jpy','fx_usd'], 'auto_update':False,'update_minutes':60,'analysis_days':90,'anomaly_percent':2.5}
    assert client.put('/api/market/settings',json=saved).json()==saved
    with TestClient(app.app) as second:
        assert second.get('/api/market/settings').json()==saved
    assert client.put('/api/market/settings',json={**saved,'update_minutes':1}).status_code==422
    assert client.put('/api/market/settings',json={**saved,'provider':'https://attacker.test'}).status_code==422
    assert client.put('/api/market/settings',json={**saved,'tracked_markets':['fx_usd']}).status_code==400
    assert client.put('/api/market/settings',json={**saved,'compare_markets':['fx_usd','fx_usd']}).status_code==422


def test_cbc_real_shape_cross_rates_and_idempotence(client,monkeypatch):
    calls=[]
    def fake(url):calls.append(url);return payload()
    monkeypatch.setattr(sources,'fetch_json',fake)
    result=client.post('/api/market/sync').json()
    assert result['ok'] and result['imported']==120
    data=client.get('/api/market/overview').json()['series']
    prices={s['latest']['instrument']:s['latest']['price'] for s in data}
    assert prices=={'fx_usd':32,'fx_jpy':.2,'fx_cny':4}
    assert all(s['latest']['publication_known']==0 for s in data)
    assert all(s['latest']['delay']=='delayed' for s in data)
    assert client.post('/api/market/sync').json()['imported']==0
    assert len(calls)==1
    with app.connect() as c:c.execute("UPDATE market_source_status SET checked_at='2020-01-01T00:00:00+00:00'")
    assert client.post('/api/market/sync').json()['imported']==0
    with app.connect() as c:assert c.execute('SELECT COUNT(*) FROM market_quotes').fetchone()[0]==120
    statuses=client.get('/api/market/sources').json()['sources']
    assert next(x for x in statuses if x['instrument']=='fx_usd')['status']=='success'
    assert next(x for x in statuses if x['instrument']=='comex_hg')['status']=='manual'


def test_failure_retains_old_values_and_success_time(client,monkeypatch):
    monkeypatch.setattr(sources,'fetch_json',lambda url:payload(stale=9))
    assert client.post('/api/market/sync').json()['ok']
    before=market.records()
    old=client.get('/api/market/sources').json()['sources'][-3:]
    assert all(s['status']=='delayed' for s in old)
    with app.connect() as c:c.execute("UPDATE market_source_status SET checked_at='2020-01-01T00:00:00+00:00'")
    def fail(url):raise URLError('synthetic offline fixture')
    monkeypatch.setattr(sources,'fetch_json',fail)
    result=client.post('/api/market/sync').json()
    assert not result['ok']
    assert market.records()==before
    status=client.get('/api/market/sources').json()['sources'][-3:]
    assert all(s['status']=='unavailable' for s in status)
    assert [s['success_at'] for s in old]==[s['success_at'] for s in status]


def test_malformed_response_no_partial_write(client,monkeypatch):
    broken=payload();broken['data']['dataSets'][0][1]='NaN'
    monkeypatch.setattr(sources,'fetch_json',lambda url:broken)
    assert not client.post('/api/market/sync').json()['ok']
    assert market.records()==[]


def test_source_revision_never_overwrites_existing_quote(client,monkeypatch):
    monkeypatch.setattr(sources,'fetch_json',lambda url:payload(days=1))
    client.post('/api/market/sync')
    original=market.records()
    revised=payload(days=1);revised['data']['dataSets'][0][1]='99'
    monkeypatch.setattr(sources,'fetch_json',lambda url:revised)
    with app.connect() as c:c.execute("UPDATE market_source_status SET checked_at='2020-01-01T00:00:00+00:00'")
    result=client.post('/api/market/sync').json()
    assert result['conflicts']==3
    assert market.records()==original


def test_scheduler_frequency_and_no_selected_automatic_market(client,monkeypatch):
    calls=[]
    monkeypatch.setattr(sources,'fetch_json',lambda url:(calls.append(url) or payload(days=1)))
    assert sources.synchronize(force=False)['imported']==3
    assert sources.synchronize(force=False)['imported']==0
    assert len(calls)==1
    settings=client.get('/api/market/settings').json()
    settings.update(tracked_markets=['fh_scrap','fh_rebar'],compare_markets=['fh_scrap','fh_rebar'])
    client.put('/api/market/settings',json=settings)
    assert sources.synchronize()['imported']==0
    assert len(calls)==1


def test_history_uses_saved_analysis_period(client,monkeypatch):
    monkeypatch.setattr(sources,'fetch_json',lambda url:payload(days=100))
    client.post('/api/market/sync')
    key=client.get('/api/market/overview').json()['series'][0]['key']
    values=client.get('/api/market/settings').json()
    client.put('/api/market/settings',json={**values,'analysis_days':30})
    assert len(client.get('/api/market/history',params={'series':key}).json()['points'])==31


def test_frankfurter_direction_and_schema():
    day=datetime.now(sources.TAIPEI).date()
    rows=[{'date':day.isoformat(),'base':'USD','quote':c,'rate':v} for c,v in [('TWD',32),('JPY',160),('CNY',8)]]
    values,_=sources.parse_frankfurter(rows,day)
    assert [v[2] for v in values]==[32,.2,4]
    with pytest.raises(ValueError):sources.parse_frankfurter({'rates':rows},day)


def test_update_lock_and_backup_settings(client):
    assert sources.UPDATE_LOCK.acquire(False)
    try:assert client.post('/api/market/sync').status_code==409
    finally:sources.UPDATE_LOCK.release()
    values=client.get('/api/market/settings').json()
    client.put('/api/market/settings',json={**values,'analysis_days':90})
    snapshot=client.get('/api/portable-backup').content
    client.put('/api/market/settings',json={**values,'analysis_days':180})
    assert client.post('/api/restore',files={'file':('snapshot.zip',snapshot)}).status_code==200
    assert client.get('/api/market/settings').json()['analysis_days']==90


def test_background_worker_starts_and_stops(client,monkeypatch):
    import threading
    import metal_engine
    called=threading.Event()
    monkeypatch.setenv('HONGZHOU_DISABLE_SCHEDULER','0')
    monkeypatch.setattr(sources,'synchronize',lambda force:called.set())
    monkeypatch.setattr(metal_engine,'sync',lambda force:None)
    stop,thread=sources.start_scheduler()
    try:assert called.wait(2)
    finally:stop.set();thread.join(2)
    assert not thread.is_alive()


def test_currency_conversion_does_not_use_disabled_automatic_provider(client,monkeypatch):
    monkeypatch.setattr(sources,'fetch_json',lambda url:payload(days=1))
    client.post('/api/market/sync')
    now=datetime.now(timezone.utc)
    quote=market.Quote(instrument='comex_hg',date=now.astimezone(sources.TAIPEI).date(),price=4,currency='USD',unit='lb',
        specification='TEST ONLY',contract='TEST',source='TEST FIXTURE',source_url='https://example.com/test',
        license_note='Synthetic automated fixture',delay='unknown',published_at=now)
    market.insert_quotes([quote],'manual')
    groups=market.grouped()
    row=next(rows[-1] for rows in groups.values() if rows[-1]['instrument']=='comex_hg')
    assert market.convert(row,groups)['value']==pytest.approx(4*32*1000/.45359237)
    settings=client.get('/api/market/settings').json()
    client.put('/api/market/settings',json={**settings,'provider':'frankfurter'})
    assert market.convert(row,groups) is None
