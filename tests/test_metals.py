"""Synthetic fixtures only, never installed as application market data."""
import io,json,zipfile
from datetime import datetime,timezone
from xml.sax.saxutils import escape
import pytest
from fastapi.testclient import TestClient
import app,market,metal_engine as engine,metal_analysis as analysis


@pytest.fixture
def client():
    with TestClient(app.app) as c:yield c


def workbook(bad_unit=False):
    def sheet(rows):
        body=[]
        for i,row in enumerate(rows,1):
            cells=[]
            for j,value in enumerate(row):
                address=chr(65+j)+str(i)
                if value is None:continue
                cells.append(f'<c r="{address}"><v>{value}</v></c>' if isinstance(value,(int,float)) else f'<c r="{address}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
            body.append(f'<row r="{i}">'+''.join(cells)+'</row>')
        return '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(body)+'</sheetData></worksheet>'
    rows=[['TEST ONLY'],['nominal'],[None],['Updated on TEST'],[None,'Copper','Aluminum','Nickel','Iron ore, cfr spot'],[None,'BAD' if bad_unit else '($/mt)','($/mt)','($/mt)','($/dmtu)']]
    for i in range(72):
        year=2020+i//12;month=i%12+1
        rows.append([f'{year}M{month:02d}',100+i*i*.1,50+i*.5,200+i*.2,80+i*.3])
    desc=[['Copper (LME), TEST ONLY'],['Aluminum (LME), TEST ONLY'],['Nickel (LME), TEST ONLY'],['Iron ore, spot in US dollar/dry ton, TEST ONLY']]
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Monthly Prices" sheetId="1" r:id="rId1"/><sheet name="Description" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',sheet(rows));z.writestr('xl/worksheets/sheet2.xml',sheet(desc))
    return out.getvalue()


def seed():
    parsed,updated=engine.parse_worldbank(workbook());return engine.store('worldbank',parsed,updated)


def test_worldbank_parse_store_and_vintage_preservation(client):
    assert seed()==(288,0)
    assert seed()==(0,0)
    assert len(client.get('/api/metals/series').json())==4
    assert engine.observations('wb_copper')[-1]['period']=='2025-12'
    parsed,updated=engine.parse_worldbank(workbook());parsed[0][1][-1]=('2025-12',999,'revised test fixture')
    assert engine.store('worldbank',parsed,updated)==(1,1)
    with app.connect() as c:assert c.execute("SELECT COUNT(*) FROM metal_observations WHERE series_id='wb_copper' AND period='2025-12'").fetchone()[0]==2
    with pytest.raises(ValueError):engine.parse_worldbank(workbook(True))


def test_month_changes_alignment_and_leads(client):
    seed()
    trend=client.get('/api/metals/trend',params={'series':'wb_copper','months':36}).json()
    assert len(trend['points'])==36
    assert set(trend['changes'])=={'1','3','6','12'}
    assert trend['changes']['3']['base_period']=='2025-09'
    expected=(100+71*71*.1)/(100+68*68*.1)-1
    assert trend['changes']['3']['percent']==pytest.approx(expected*100)
    assert trend['missing_fx']==36
    query={'a':'wb_copper','b':'wb_aluminum','months':60,'mode':'index'}
    compare=client.post('/api/metals/compare',json=query).json()
    assert compare['points'][0]['a']==100 and compare['points'][0]['b']==100
    leading=client.post('/api/metals/leading',json=query).json()
    assert [r['samples'] for r in leading['results']]==[59,58,57,56]
    assert leading['predictive_evidence'] is False
    assert client.post('/api/metals/compare',json={**query,'a':'wb_aluminum','b':'wb_iron_ore','mode':'twd'}).status_code==400


def test_bls_index_and_taiwan_encoding(client):
    data={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'WPU102301','data':[{'year':'2025','period':'M01','value':'123','footnotes':[{'text':'Preliminary'}]},{'year':'2025','period':'M13','value':'999'}]}]}}
    parsed,_=engine.parse_bls(json.dumps(data).encode());engine.store('bls',parsed,None)
    assert parsed[0][0]['currency']=='INDEX' and len(parsed[0][1])==1
    raw='調查時間,調查項目,單位,北區,中區,南區\r\r\n11404,測試鋼筋,T,100,110,120\r\r\n'.encode('cp950')
    values,_=engine.parse_taiwan(raw,'tw_rebar')
    assert len(values)==3 and values[0][1][0][0]=='2025-04'
    assert '豐興' in values[0][0]['notes']
    plate='調查時間,調查項目,單位,不分區價格\r\n11404,測試鋼板,T,27000\r\n'.encode('cp950')
    assert len(engine.parse_taiwan(plate,'tw_plate')[0])==1


def test_partial_source_failure_retains_data(client,monkeypatch):
    client.put('/api/metals/settings',json={'providers':['worldbank','bls']})
    def fake(provider):
        if provider=='bls':raise OSError('TEST ONLY source offline')
        return workbook(),None
    monkeypatch.setattr(engine,'download',fake)
    result=client.post('/api/metals/sync').json()
    assert result['ok'] is False and len(client.get('/api/metals/series').json())==4
    assert client.get('/api/metals/status').json()[1]['error']
    assert client.get('/').status_code==200


def test_month_fx_has_no_future_or_incomplete_month_fill(client):
    seed()
    for day in range(1,11):
        p=market.Quote(instrument='fx_usd',date=f'2025-12-{day:02d}',price=32,currency='TWD',unit='fx',specification='TEST',contract='TEST',source='TEST FIXTURE',source_url='https://example.com/test',license_note='Synthetic fixture',delay='unknown',published_at=datetime(2025,12,day,20,tzinfo=timezone.utc))
        market.insert_quotes([p],'manual')
    trend=client.get('/api/metals/trend',params={'series':'wb_copper','months':12}).json()
    assert trend['points'][-1]['twd_value']==pytest.approx(trend['points'][-1]['value']*32)
    assert trend['points'][-2]['twd_value'] is None


def test_settings_backup_restart_and_missing_month(client):
    seed()
    assert client.put('/api/metals/settings',json={'providers':['worldbank'],'months':36,'threshold':7}).status_code==200
    with TestClient(app.app) as other:assert other.get('/api/metals/settings').json()['threshold']==7
    backup=client.get('/api/portable-backup').content
    client.put('/api/metals/settings',json={'threshold':20})
    assert client.post('/api/restore',files={'file':('backup.zip',backup)}).status_code==200
    assert client.get('/api/metals/settings').json()['threshold']==7
    rows=[{'period':'2025-01','value':100},{'period':'2025-03','value':120}]
    assert analysis.changes(rows,1) is None


def test_today_download_is_not_backfilled_into_past_weekly_prediction(client):
    seed()
    weekly=[{'date':'2025-01-01','price':100,'published_at':'2025-01-01T20:00:00+00:00'},
            {'date':'2025-01-08','price':110,'published_at':'2025-01-08T20:00:00+00:00'}]
    studies=analysis.weekly_factor_research(weekly,[])
    assert len(studies)==4
    assert all(s['known_origins']==0 and s['samples']==0 for s in studies)


def test_additive_v3_upgrade_keeps_original_quotes_and_snapshots(client):
    client.post('/api/customers',json={'name':'TEST UPGRADE'})
    with app.connect() as c:
        c.execute('DROP TABLE metal_observations');c.execute('DROP TABLE metal_series');c.execute('DROP TABLE metal_updates')
        c.execute('DELETE FROM schema_migrations WHERE version=4')
    app.setup_database()
    assert client.get('/api/customers').json()[0]['name']=='TEST UPGRADE'
    assert client.get('/api/metals/series').json()==[]
    snapshots=list(app.BACKUPS.glob('hongzhou-*.db'));assert snapshots
    with app.connect(snapshots[-1]) as c:
        assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]==3
        assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1
