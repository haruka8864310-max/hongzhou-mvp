from fastapi.testclient import TestClient
import app,decision_dashboard as dd
from test_scrap_analysis import prepare


def test_home_is_only_five_sections_without_legacy_forms():
    with TestClient(app.app) as c:
        html=c.get('/market-brief').text
        assert html.count('<section ')==5
        assert '<form' not in html and 'compareChart' not in html and 'globalSummary' not in html
        assert '中文鋼鐵新聞' in html and '豐興下週漲跌分析' in html
        assert 'quoteform' in c.get('/advanced').text
        result=c.get('/api/dashboard').json()
        assert result['markets']==[] and len(result['missing'])==3
        assert result['fenghsin']['points']==[]


def test_current_real_shape_and_no_probability_in_factor_brief(monkeypatch):
    with TestClient(app.app) as c:
        prepare(monkeypatch)
        result=c.get('/api/dashboard?months=0').json()
        assert result['fenghsin']['total_count']==2
        assert len(result['markets'])==1
        assert result['markets'][0]['label'].startswith('日本')
        assert result['markets'][0]['stale']
        assert result['direction']['up']==[] and result['direction']['down']==[]
        assert result['direction']['warnings']
        assert c.get('/api/dashboard?months=-1').status_code==422


def test_partial_update_returns_each_error_without_blank_page(monkeypatch):
    with TestClient(app.app) as c:
        monkeypatch.setattr(dd.china_sources,'sync',lambda *_:(_ for _ in ()).throw(RuntimeError('TEST official offline')))
        monkeypatch.setattr(dd.news_sources,'sync',lambda *_:{'ok':False,'message':'TEST news HTTP403'})
        monkeypatch.setattr(dd.fenghsin,'sync',lambda *_:{'ok':True,'message':'TEST no change'})
        monkeypatch.setattr(dd.japan_sources,'sync',lambda *_:{'ok':True,'message':'TEST no change'})
        monkeypatch.setattr(dd.metal_engine,'sync',lambda *a,**k:{'ok':True,'message':'TEST no change'})
        r=c.post('/api/dashboard/update').json()
        assert not r['ok'] and len(r['results'])==5
        assert 'official offline' in r['results'][0]['message']
        assert c.get('/api/dashboard').status_code==200 and c.get('/market-brief').status_code==200
