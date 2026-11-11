"""Synthetic analysis fixtures in autouse isolated DB, never production."""
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
import app, fenghsin, global_market as gm, scrap_analysis as sa
from test_fenghsin import network


def tokyo(day='2025-01-09',price=20000,stamp='2025-01-10T00:00:00Z'):
    with app.connect() as c:
        gm.insert(c,dict(market='jp_tokyo',grade='岡山工場／二級',delivery_term='TEST land',
            currency='JPY',unit='t',price=price,quote_date=day,published_at=None,frequency='observed',
            source='TEST Tokyo',source_url='https://example.com/test',license_note='Synthetic fixture'),
            stamp,day+str(price),'automatic')


def prepare(monkeypatch):
    network(monkeypatch)
    fenghsin.sync()
    tokyo('2025-01-02',19000)
    tokyo()


def test_catalog_defaults_and_dual_native_curves(monkeypatch):
    with TestClient(app.app) as c:
        prepare(monkeypatch)
        cat=c.get('/api/scrap-analysis/catalog').json()
        assert cat['defaults']['a'].startswith('fh:scrap:')
        assert cat['defaults']['b'].startswith('g:')
        r=c.get('/api/scrap-analysis/compare?mode=native&months=0').json()
        assert len(r['curves']['a'])==len(r['curves']['b'])==2
        assert r['a']['currency']=='TWD' and r['b']['currency']=='JPY'
        assert all(p['source_url'] for p in r['curves']['b'])
        assert 'scrapPanel' in c.get('/advanced').text
        # The page has unique FH center and legacy input IDs.
        assert c.get('/advanced').text.count('id="fhProduct"')==1
        assert c.get('/advanced').text.count('id="fhPeriod"')==1
        summary=c.get('/api/scrap-analysis/summary').json()
        assert len(summary['items'])==1 and summary['items'][0]['name']=='東京製鐵廢鋼採購'


def test_index_base_age_and_no_future_quote(monkeypatch):
    with TestClient(app.app) as c:
        prepare(monkeypatch)
        r=c.get('/api/scrap-analysis/compare?months=0').json()
        assert r['aligned'][0]['a']==r['aligned'][0]['b']==100
        for pair in r['aligned']:
            for key in ['a','b']:
                p=pair[key+'_observation']
                assert p['date']<=pair['date'] and 0<=p['age_days']<=35
        assert '非當年' in r['warning']
        # Today's collected FH snapshot must not appear in past information set.
        cat=c.get('/api/scrap-analysis/catalog?as_of=2025-01-12T23:59:59Z').json()
        assert not any(s['origin']=='fenghsin_facts' for s in cat['items'])


def test_fx_uses_previous_quote_only_and_missing_reason(monkeypatch):
    with TestClient(app.app) as c:
        prepare(monkeypatch)
        r=c.get('/api/scrap-analysis/compare?mode=twd&months=0').json()
        assert len(r['curves']['a'])==2 and not r['curves']['b']
        assert '匯率' in r['missing']['b'][0]['reason']
        with app.connect() as con:
            for day,price in [('2025-01-01',.2),('2025-01-08',.25),('2025-01-10',.9)]:
                gm.insert(con,dict(market='fx_jpy',grade='TEST fx',delivery_term='TEST',currency='TWD',
                    unit='TWD per JPY',price=price,quote_date=day,frequency='daily',source='TEST FX',
                    source_url='https://example.com/test',license_note='TEST'),
                    '2025-01-11T00:00:00Z',day,'automatic')
        r=c.get('/api/scrap-analysis/compare?mode=twd&months=0').json()
        assert [p['value'] for p in r['curves']['b']]==[3800,5000]
        assert all(p['fx']['date']<=p['date'] for p in r['curves']['b'])


def test_validation_and_settings_restart(monkeypatch):
    with TestClient(app.app) as c:
        prepare(monkeypatch)
        for query in ['mode=bad','months=-1','max_age_days=0','as_of=bad','as_of=2025-01-01','a=missing']:
            assert c.get('/api/scrap-analysis/compare?'+query).status_code==422
        value={'months':36,'mode':'native','max_age_days':14}
        assert c.put('/api/scrap-analysis/settings',json=value).status_code==200
        backup=c.get('/api/portable-backup').content
        assert c.post('/api/restore',files={'file':('backup.zip',backup)}).status_code==200
    with TestClient(app.app) as c:
        assert c.get('/api/scrap-analysis/settings').json()['months']==36


def test_monthly_adapter_reads_original_versions_without_ledger_duplicates():
    from test_metals import seed
    with TestClient(app.app) as c:
        seed()
        gm.capture_existing()
        data=sa.datasets()
        monthly=[s for s in data.values() if s['origin']=='metal_observations']
        assert len(monthly)==4
        assert all(len(s['points'])==72 for s in monthly)
        assert not any(s['origin']=='global_quotes' for s in data.values())
        assert all(p['date']>=p['quote_date'] and p['source_url'] for s in monthly for p in s['points'])
        assert all(s['unit'] in ('t','dry_t') for s in monthly)
