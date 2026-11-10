from fastapi.testclient import TestClient
import app,market_detail as detail,metal_engine as engine
from test_metals import seed


def bulletin(**changes):
    return {'cadence':'weekly','product':'rebar','period':'2025-11-03','change':0,'source':'tymetals','specification':'TEST FIXTURE ONLY','permission_note':'Synthetic test data',**changes}


def test_monthly_detail_periods_statistics_and_comparison():
    with TestClient(app.app) as c:
        seed()
        for months in (1,3,6,12,36,0):
            r=c.get('/api/detail/view',params={'series':'m:wb_copper','months':months,'compare':'m:wb_aluminum'})
            assert r.status_code==200
            d=r.json();assert len(d['points'])==(months or 72)
            assert d['series']['frequency']=='monthly'
            assert d['statistics']['samples']==len(d['points'])
            assert d['statistics']['high']==max(p['value'] for p in d['points'])
            assert all(len(p['period'])==7 for p in d['points'])
            if months!=1:
                assert d['change']['absolute']>0
                assert d['comparison'][0]['a']==d['comparison'][0]['b']==100
        r=c.get('/api/detail/view',params={'series':'m:wb_copper','months':3,'mode':'twd'}).json()
        assert r['points']==[] and r['missing_fx']==72
        assert r['latest']['value']>0
        assert c.get('/api/detail/view?series=m:wb_copper&months=2').status_code==400
        assert c.get('/api/detail/view?series=not-found').status_code==404
        assert c.get('/api/detail/view?series=i:fh_scrap').json()['points']==[]
        assert 'detailChart' in c.get('/').text


def test_weekly_adjustment_and_monthly_never_merged_and_backup(tmp_path):
    with TestClient(app.app) as c:
        a=c.post('/api/detail/steel',json=bulletin()).json()
        b=c.post('/api/detail/steel',json=bulletin(cadence='monthly',period='2025-10',source='metaltrade',price=123,change=None)).json()
        assert a['key']!=b['key']
        weekly=c.get('/api/detail/view',params={'series':a['key']}).json()
        assert weekly['latest'] is None and weekly['points']==[]
        assert weekly['latest_announcement']['change']==0
        monthly=c.get('/api/detail/view',params={'series':b['key'],'compare':a['key']}).json()
        assert len(monthly['points'])==1 and monthly['comparison']==[]
        assert '頻率不同' in monthly['compare_note']
        assert c.post('/api/detail/steel',json=bulletin()).status_code==409
        assert c.post('/api/detail/steel',json=bulletin(period='2099-01-01')).status_code==422
        assert c.post('/api/detail/steel',json=bulletin(change=None)).status_code==422
        assert c.post('/api/detail/steel',json=bulletin(price=-1)).status_code==422
        backup=c.get('/api/portable-backup').content
        # A malformed bulletin backup must fail validation before replacing data.
        invalid=tmp_path/'invalid.db'
        with app.connect() as src,app.connect(invalid) as dest:src.backup(dest)
        with app.connect(invalid) as con:con.execute("UPDATE market_settings SET value='{}' WHERE name='steel_bulletins'")
        try:
            app.validate_database(invalid)
            assert False,'invalid bulletin accepted'
        except ValueError:pass
        restored=c.post('/api/restore',files={'file':('backup.zip',backup)})
        assert restored.status_code==200,restored.text
    with TestClient(app.app) as c:
        assert len(c.get('/api/detail/steel').json()['items'])==2
        assert c.post('/api/restore',files={'file':('backup.zip',backup)}).status_code==200
        assert len(c.get('/api/detail/steel').json()['items'])==2
