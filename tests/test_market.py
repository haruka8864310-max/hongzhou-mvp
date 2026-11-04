"""All prices here are synthetic test fixtures, never loaded into application data."""
import csv
import io
import sqlite3
import pytest
from fastapi.testclient import TestClient
import app
import market


@pytest.fixture
def client():
    with TestClient(app.app) as c:
        yield c


def quote(instrument='fh_scrap', day='2025-01-01', price=100, **kwargs):
    item = market.INSTRUMENTS[instrument]
    return dict(instrument=instrument, date=day, price=price, currency=item['currency'], unit=item['unit'],
                specification='TEST ONLY', contract='TEST', source='TEST FIXTURE',
                source_url='https://example.com/test-only', license_note='Synthetic automated test fixture',
                delay='unknown', published_at=day+'T16:00:00+08:00', **kwargs)


def add(client, **kwargs):
    r = client.post('/api/market/quotes', json=quote(**kwargs))
    assert r.status_code == 200, r.text


def compare(client, **kwargs):
    keys = [s['key'] for s in client.get('/api/market/overview').json()['series'] if s['latest']['unit']!='fx']
    query = dict(series=keys, start='2025-01-01', end='2025-02-01', mode='index')
    query.update(kwargs)
    return client.post('/api/market/compare', json=query)


def test_empty_dashboard_and_assets(client):
    assert client.get('/api/market/overview').json()['series'] == []
    assert len(client.get('/api/market/catalog').json()['instruments']) == 11
    for path in ['/', '/assets/market.js', '/assets/market.css', '/legacy']:
        assert client.get(path).status_code == 200
    for title in ['市場總覽','跨市場比較','豐興行情研究','市場機會雷達','quoteform','csvform']:
        assert title in client.get('/').text
    assert len(client.get('/api/market/template').text.splitlines()) == 1


@pytest.mark.parametrize('changes', [{'price':0},{'price':-1},{'source_url':'javascript:alert(1)'},{'specification':'   '},
    {'date':'2025-02-30'},{'published_at':'2025-01-01T16:00:00'},{'date':'2999-01-01'},
    {'delay':'realtime'},{'instrument':'unknown'},{'unit':'fx'},{'license_note':''},
    {'published_at':'2999-01-01T16:00:00+08:00'}])
def test_quote_validation(client,changes):
    assert client.post('/api/market/quotes',json={**quote(),**changes}).status_code == 422


def test_overview_changes_and_contract_separation(client):
    add(client,day='2025-01-01',price=100)
    add(client,day='2025-01-24',price=110)
    add(client,day='2025-01-31',price=121)
    data = client.get('/api/market/overview').json()['series'][0]
    assert data['days7']['percent'] == pytest.approx(10)
    assert data['days30']['percent'] == pytest.approx(21)
    assert data['stale'] is True
    assert data['latest']['source'] == 'TEST FIXTURE'
    assert client.post('/api/market/quotes',json=quote(day='2025-01-31',price=999)).status_code == 409
    assert client.post('/api/market/quotes',json={**quote(), 'contract':'OTHER'}).status_code == 200
    assert len(client.get('/api/market/overview').json()['series']) == 2


def csv_bytes(rows):
    s=io.StringIO();w=csv.DictWriter(s,fieldnames=market.CSV_FIELDS);w.writeheader();w.writerows(rows)
    return ('\ufeff'+s.getvalue()).encode('utf-8')


def test_csv_atomic_import_duplicate_and_bad_row(client):
    payload=csv_bytes([quote(),quote(day='2025-01-08',price=-1)])
    assert client.post('/api/market/import',files={'file':('a.csv',payload)}).status_code == 400
    assert client.get('/api/market/overview').json()['series'] == []
    payload=csv_bytes([quote(),quote(day='2025-01-08',price=110)])
    assert client.post('/api/market/import',files={'file':('a.csv',payload)}).json()['imported'] == 2
    payload=csv_bytes([quote(day='2025-01-15'),quote()])
    assert client.post('/api/market/import',files={'file':('a.csv',payload)}).status_code == 409
    assert client.get('/api/market/overview').json()['series'][0]['count'] == 2
    assert client.post('/api/market/import',files={'file':('a.csv',b'wrong\n1')}).status_code == 400


def test_exact_asof_index_and_no_future_fill(client):
    add(client,day='2025-01-01',price=100)
    add(client,day='2025-01-08',price=120)
    add(client,instrument='jp_h2',day='2025-01-02',price=200)
    add(client,instrument='jp_h2',day='2025-01-08',price=220)
    result=compare(client).json()
    assert [p['date'] for p in result['points']] == ['2025-01-08']
    assert [e['value'] for e in result['points'][0]['entries']] == [100,100]
    result=compare(client,alignment='asof',max_age_days=7).json()
    assert [p['date'] for p in result['points']] == ['2025-01-02','2025-01-08']
    assert result['points'][0]['entries'][0]['quote_date']=='2025-01-01'
    assert result['correlation'] is None


def test_twd_conversion_and_missing_fx(client):
    add(client,instrument='fh_scrap',price=1000)
    add(client,instrument='comex_hg',price=4)
    assert compare(client,mode='twd').json()['points'] == []
    add(client,instrument='fx_usd',day='2025-01-02',price=99)
    # Future FX must not be used for Jan 1.
    assert compare(client,mode='twd').json()['points'] == []
    add(client,instrument='fx_usd',day='2025-01-01',price=32)
    result=compare(client,mode='twd').json()
    assert result['points'][0]['entries'][1]['value'] == pytest.approx(4*32*1000/0.45359237)
    assert result['points'][0]['entries'][1]['fx_date']=='2025-01-01'


def test_short_ton_conversion_and_stale_fx(client):
    add(client,day='2025-01-10')
    add(client,instrument='us_scrap',day='2025-01-10',price=300)
    add(client,instrument='fx_usd',day='2025-01-01',price=32)
    assert compare(client,mode='twd').json()['points']==[]
    add(client,instrument='fx_usd',day='2025-01-10',price=32)
    assert compare(client,mode='twd').json()['points'][0]['entries'][1]['value']==pytest.approx(300*32*1000/907.18474)


def test_backup_preserves_market_and_legacy(client):
    add(client)
    client.post('/api/customers',json={'name':'TEST ONLY'})
    snapshot=client.get('/api/portable-backup').content
    add(client,day='2025-01-08')
    assert client.post('/api/restore',files={'file':('backup.zip',snapshot)}).status_code==200
    assert client.get('/api/market/overview').json()['series'][0]['count']==1
    assert len(client.get('/api/customers').json())==1


def test_additive_upgrade_keeps_original_rows(client):
    client.post('/api/customers',json={'name':'TEST ONLY'})
    with app.connect() as c:
        c.execute('DROP TABLE market_quotes')
        c.execute('DELETE FROM schema_migrations WHERE version>1')
    app.setup_database()
    assert list(app.BACKUPS.glob('hongzhou-*.db'))
    assert client.get('/api/customers').json()[0]['name']=='TEST ONLY'
    assert client.get('/api/market/overview').json()['series']==[]


def test_research_insufficient_and_future_leakage(client):
    from datetime import timedelta, date
    add(client)
    key=client.get('/api/market/overview').json()['series'][0]['key']
    result=client.post('/api/market/research',json={'series':key}).json()
    assert result['probabilities'] is None
    assert '無法可靠預測' in result['message']
    # Exactly 53 weekly points, synthetic deterministic changes.
    rows=[quote(day=(date(2025,1,1)+timedelta(days=7*i)).isoformat(),price=100+i%5) for i in range(1,53)]
    assert client.post('/api/market/import',files={'file':('test.csv',csv_bytes(rows))}).status_code==200
    original=client.post('/api/market/research',json={'series':key}).json()
    assert original['backtest_samples']==26
    assert sum(sum(row.values()) for row in original['confusion_matrix'].values())==26
    assert sum(original['probabilities'].values())==pytest.approx(1)
    assert original['backtest'][0]['training_transitions']==26
    assert all(r['origin']<r['target_date'] for r in original['backtest'])
    # Changing the final outcome must not change earlier predictions.
    with app.connect() as c:
        c.execute("UPDATE market_quotes SET price=9999 WHERE date=(SELECT MAX(date) FROM market_quotes)")
    modified=client.post('/api/market/research',json={'series':key}).json()
    assert [r['predicted'] for r in original['backtest']]==[r['predicted'] for r in modified['backtest']]
    assert original['stale'] is True


def test_research_rejects_irregular_weeks(client):
    from datetime import timedelta, date
    rows=[quote(day=(date(2025,1,1)+timedelta(days=i)).isoformat()) for i in range(53)]
    client.post('/api/market/import',files={'file':('test.csv',csv_bytes(rows))})
    key=client.get('/api/market/overview').json()['series'][0]['key']
    result=client.post('/api/market/research',json={'series':key}).json()
    assert result['probabilities'] is None
    assert '間隔' in result['message']


def test_radar_threshold_persistence_stale_exclusion_and_divergence(client):
    from datetime import date, timedelta
    current=date.today()
    previous=current-timedelta(days=7)
    for instrument,values in [('fh_scrap',[100,110]),('jp_h2',[100,90])]:
        for day,price in zip([previous,current],values):
            payload=quote(instrument=instrument,day=day.isoformat(),price=price)
            payload['published_at']=day.isoformat()+'T00:00:00+08:00'
            assert client.post('/api/market/quotes',json=payload).status_code==200
    add(client,instrument='comex_hg',day='2025-01-01',price=1)
    add(client,instrument='comex_hg',day='2025-01-02',price=100)
    result=client.post('/api/market/radar',json={'move':5,'fx':2,'streak':3}).json()
    assert len(result['signals'])==1
    assert '背離' in result['signals'][0]['title']
    assert 'TEST FIXTURE' in result['signals'][0]['time']
    assert client.get('/api/market/radar-settings').json()['fx']==2
    assert client.post('/api/market/radar',json={'move':0}).status_code==422
    assert client.post('/api/market/radar',json={'move':99}).json()['signals']==[]


def test_correlations_require_matched_intervals_and_lag(client):
    from datetime import date, timedelta
    start=date(2025,1,1)
    for instrument, offset in [('fh_scrap',0),('jp_h2',1)]:
        for i in range(12):
            add(client,instrument=instrument,day=(start+timedelta(days=i+offset)).isoformat(),price=100+i*i)
    result=compare(client,lag=1).json()
    assert result['correlation_samples']==11
    assert result['correlation']==pytest.approx(1)
    keys=[s['key'] for s in client.get('/api/market/overview').json()['series']]
    assert compare(client,series=[keys[0],keys[0]]).status_code==400
    assert compare(client,start='2025-02-01',end='2025-01-01').status_code==400


def test_ambiguous_fx_series_not_mixed(client):
    add(client)
    add(client,instrument='comex_hg')
    add(client,instrument='fx_usd',price=32)
    assert client.post('/api/market/quotes',json={**quote(instrument='fx_usd',price=33),'specification':'OTHER SOURCE'}).status_code==200
    assert compare(client,mode='twd').json()['points']==[]


def test_restore_v1_adds_tables_without_losing_rows(client,tmp_path):
    client.post('/api/customers',json={'name':'TEST LEGACY'})
    old=app.backup_database(as_zip=False)
    with app.connect(old) as c:
        c.execute('DROP TABLE market_quotes')
        c.execute('DROP TABLE market_settings')
        c.execute('DELETE FROM schema_migrations WHERE version>1')
    assert client.post('/api/restore',files={'file':('old.db',old.read_bytes())}).status_code==200
    assert client.get('/api/market/overview').json()['series']==[]
    assert client.get('/api/customers').json()[0]['name']=='TEST LEGACY'
    assert list(app.BACKUPS.glob('before-restore-*.db'))


def test_copper_fx_and_streak_signals(client):
    from datetime import datetime,timedelta,timezone
    current=datetime.now(timezone(timedelta(hours=8))).date()
    for instrument,prices in [('comex_hg',[100,110,121,133.1]),('fx_usd',[32,33])]:
        for i,price in enumerate(prices):
            day=(current-timedelta(days=len(prices)-1-i)).isoformat()
            payload=quote(instrument=instrument,day=day,price=price)
            payload['published_at']=day+'T00:00:00+08:00'
            assert client.post('/api/market/quotes',json=payload).status_code==200
    result=client.post('/api/market/radar',json={'move':5,'fx':1,'streak':3}).json()
    titles=' '.join(s['title'] for s in result['signals'])
    assert '波動提示' in titles
    assert '連續走勢' in titles
    assert '匯率風險' in titles
    assert len(result['signals'])==3


def test_late_publications_rejected_for_research(client):
    from datetime import date,timedelta
    rows=[]
    for i in range(53):
        day=date(2025,1,1)+timedelta(days=7*i)
        payload=quote(day=day.isoformat())
        payload['published_at']=(day+timedelta(days=4)).isoformat()+'T16:00:00+08:00'
        rows.append(payload)
    assert client.post('/api/market/import',files={'file':('test.csv',csv_bytes(rows))}).status_code==200
    key=client.get('/api/market/overview').json()['series'][0]['key']
    result=client.post('/api/market/research',json={'series':key}).json()
    assert result['probabilities'] is None
    assert '發布較晚' in result['message']
