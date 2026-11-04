from fastapi.testclient import TestClient
import app
import metal_engine as engine
import metal_analysis as analysis


def test_briefing_countries_do_not_invent_prices(monkeypatch):
    monkeypatch.setattr(engine,'all_series',lambda:[])
    with TestClient(app.app) as c:
        for country in ('us','jp','au'):
            result=c.get('/api/metals/briefing',params={'country':country})
            assert result.status_code==200
            assert result.json()['items']==[]
        assert c.get('/api/metals/briefing?country=invalid').status_code==400
        page=c.get('/').text
        assert 'id="decisionCopper"' in page
        assert 'id="decisionActions"' in page
        assert 'data-country="jp"' in page
        assert 'assets/decision.js' in page


def test_briefing_preserves_units_dates_and_staleness(monkeypatch):
    monkeypatch.setattr(engine,'all_series',lambda:[{'id':'wb_copper','provider':'worldbank','name':'TEST copper'}])
    monkeypatch.setattr(engine,'observations',lambda _: [{'period':'2020-01','value':10},{'period':'2020-02','value':12}])
    monkeypatch.setattr(analysis,'radar',lambda:{'signals':[]})
    result=analysis.briefing('jp')
    assert result['items'][0]['change']['percent'] > 19
    assert result['items'][0]['stale'] is True
    assert result['reference'][1].startswith('https://www.meti.go.jp/')
