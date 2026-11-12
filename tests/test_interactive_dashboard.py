from fastapi.testclient import TestClient
import app
from test_scrap_analysis import prepare


def test_chart_reads_original_dates_and_sources(monkeypatch):
    with TestClient(app.app) as client:
        prepare(monkeypatch)
        data=client.get('/api/dashboard/chart').json()
        assert len(data['series'])>=2
        fh=next(s for s in data['series'] if s['id']=='fh_scrap')
        assert len(fh['points'])==2
        assert all(p['source_url'] and p['date'] and p['price'] for p in fh['points'])
        assert '授權' in data['futures_status']
        assert all('RB0' not in s['id'] for s in data['series'])


def test_local_chart_assets_and_pwa_do_not_cache_data():
    with TestClient(app.app) as client:
        html=client.get('/market-brief').text
        assert 'market-chart.js' in html and 'lightweight-charts.js' in html
        assert '<canvas' not in html and '<form' not in html
        worker=client.get('/sw.js')
        assert worker.status_code==200
        assert worker.headers['service-worker-allowed']=='/'
        assert 'caches.' not in worker.text
        assert client.get('/assets/manifest.webmanifest').json()['start_url']=='/'
        assert client.get('/assets/vendor/lightweight-charts/LICENSE').status_code==200


def test_sina_probe_has_no_requests_without_provider_permission(monkeypatch):
    from scripts import check_live_futures as probe
    monkeypatch.setattr(probe.http,'get',lambda *a,**k:(_ for _ in ()).throw(AssertionError('no network allowed')))
    for symbol in probe.SYMBOLS:
        result=probe.probe(symbol)
        assert not result['ok'] and result['attempts']==[]
