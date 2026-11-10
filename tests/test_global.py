import csv,io,json,sqlite3
from datetime import datetime,timezone
from fastapi.testclient import TestClient
import app,global_market as gm,metal_engine as engine,market_sources
from test_metals import seed


def quote(**changes):
    return dict(market='jp_fob',grade='TEST H2',delivery_term='FOB TEST port',currency='JPY',unit='t',price=100,quote_date='2025-01-01',published_at='2025-01-01T01:00:00+00:00',frequency='weekly',source='TEST ONLY',source_url='https://example.com/test',license_note='Synthetic test fixture',**changes)


def test_append_revisions_and_no_future_information(monkeypatch):
    with TestClient(app.app) as c:
        monkeypatch.setattr(gm,'now',lambda:'2026-01-01T00:00:00+00:00')
        assert c.post('/api/global/quotes',json=quote()).status_code==200
        monkeypatch.setattr(gm,'now',lambda:'2026-01-02T00:00:00+00:00')
        changed=quote();changed['price']=120
        assert c.post('/api/global/quotes',json=changed).status_code==200
        assert gm.available('2025-12-31T23:59:59Z')==[]
        assert gm.available('2026-01-01T00:00:00Z')[0]['price']==100
        assert gm.available('2026-01-02T00:00:00Z')[0]['price']==120
        assert gm.available('2026-01-02T08:00:00+08:00')[0]['price']==120
        assert gm.available()[0]['first_seen_at'].startswith('2026-01-01')
        with app.connect() as con:assert con.execute('SELECT COUNT(*) FROM global_quotes').fetchone()[0]==2
        assert c.get('/api/global/overview?as_of=2026-01-01').status_code==400
        bad=quote();bad['published_at']='2027-01-01T00:00:00Z'
        assert c.post('/api/global/quotes',json=bad).status_code==422


def test_capture_real_structure_idempotent_months_and_fx_absent():
    with TestClient(app.app) as c:
        seed();a=c.post('/api/global/capture').json();assert a['versions_added']==288
        assert c.post('/api/global/capture').json()['versions_added']==0
        copper=next(m for m in c.get('/api/global/overview').json()['items'] if m['id']=='global_copper')
        id=copper['series'][0]['id']
        assert copper['country']=='GLOBAL' and copper['series'][0]['latest']['published_at'] is None
        for n in (1,3,6,12,36,0):assert len(c.get('/api/global/history',params={'series':id,'months':n}).json()['points'])==(n or 72)
        assert c.get('/api/global/history',params={'series':id,'mode':'twd'}).json()['points']==[]
        # Today-acquired snapshots cannot appear in a past backtest.
        assert c.get('/api/global/overview?as_of=2025-12-01T00:00:00Z').json()['items'][0]['series']==[]


def test_csv_atomic_country_and_delivery_separation():
    with TestClient(app.app) as c:
        headers=list(gm.Observation.model_fields)
        output=io.StringIO();writer=csv.DictWriter(output,fieldnames=headers);writer.writeheader()
        payload=quote();payload['adjustment']=None;writer.writerow(payload)
        payload['price']=-1;writer.writerow(payload)
        assert c.post('/api/global/import',files={'file':('bad.csv',output.getvalue())}).status_code==400
        assert gm.available()==[]
        for term in ('FOB TEST port','FOB TEST other port'):
            payload=quote();payload['delivery_term']=term
            assert c.post('/api/global/quotes',json=payload).status_code==200
        rows=gm.available();assert len({r['series_id'] for r in rows})==2
        assert all(r['country']=='JP' for r in rows)
        assert c.get('/api/global/compare',params={'a':rows[0]['series_id'],'b':rows[1]['series_id']}).status_code==200
        assert c.get('/api/global/overview?country=WRONG').status_code==400


def test_v5_backup_upgrade_and_new_records_survive():
    with TestClient(app.app) as c:
        c.post('/api/global/quotes',json=quote())
        c.put('/api/global/settings',json={'country':'JP','months':36,'mode':'index'})
        backup=c.get('/api/portable-backup').content
        assert c.post('/api/restore',files={'file':('backup.zip',backup)}).status_code==200
        assert len(gm.available())==1
    with TestClient(app.app) as c:
        assert c.get('/api/global/settings').json()=={'country':'JP','months':36,'mode':'index'}
        assert len(gm.available())==1
        with app.connect() as con:
            con.execute('DROP TABLE global_quotes');con.execute('DELETE FROM schema_migrations WHERE version>=5')
        app.setup_database()
        snapshots=list(app.BACKUPS.glob('hongzhou-*.db'));assert snapshots
        with sqlite3.connect(snapshots[-1]) as con:assert con.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]==4


def test_source_failure_keeps_snapshots(monkeypatch):
    with TestClient(app.app) as c:
        seed();gm.capture_existing()
        monkeypatch.setattr(engine,'sync',lambda *a:(_ for _ in ()).throw(OSError('TEST monthly offline')))
        monkeypatch.setattr(market_sources,'synchronize',lambda *a:{'ok':False,'message':'TEST FX offline'})
        r=c.post('/api/global/refresh').json();assert r['ok'] is False
        assert len(gm.available())==288
        assert c.get('/').status_code==200


def test_file_import_is_not_reported_as_successful_http_fetch():
    from test_metals import workbook
    with TestClient(app.app) as c:
        assert c.post('/api/metals/import-file',data={'provider':'worldbank'},files={'file':('official.xlsx',workbook())}).status_code==200
        gm.capture_existing()
        assert all(r['data_status']=='imported' for r in gm.available())
        state=next(m for m in c.get('/api/global/overview').json()['items'] if m['id']=='global_copper')['state']
        assert state['last_success_at'] is None and state['last_import_at']


def test_fx_revision_is_kept_and_cross_rate_uses_same_source(monkeypatch):
    with TestClient(app.app) as c:
        cfg=market_sources.get_settings().model_dump();cfg['analysis_days']=365
        c.put('/api/market/settings',json=cfg)
        monkeypatch.setattr(market_sources,'fetch_json',lambda _:None)
        monkeypatch.setattr(market_sources,'parse_cbc',lambda *a:([('fx_usd','2025-12-01',32),('fx_jpy','2025-12-01',.2),('fx_cny','2025-12-01',4)],None))
        assert c.post('/api/market/sync').json()['ok'] is True
        gm.capture_existing()
        assert next(r for r in gm.available() if r['market']=='jpy_per_usd')['price']==160
        with app.connect() as con:con.execute("UPDATE market_source_status SET checked_at='2020-01-01T00:00:00+00:00'")
        monkeypatch.setattr(market_sources,'parse_cbc',lambda *a:([('fx_usd','2025-12-01',34),('fx_jpy','2025-12-01',.2),('fx_cny','2025-12-01',4)],None))
        assert c.post('/api/market/sync').json()['conflicts']==1
        gm.capture_existing()
        assert next(r for r in gm.available() if r['market']=='jpy_per_usd')['price']==170
        assert next(r for r in gm.available() if r['market']=='fx_usd')['price']==34
        # The original legacy quote was not overwritten by the new ledger.
        with app.connect() as con:assert con.execute("SELECT price FROM market_quotes WHERE instrument='fx_usd'").fetchone()[0]==32
