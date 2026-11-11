from fastapi.testclient import TestClient
import pytest
import app,news_sources as ns
from public_fetch import SourceError


def html():
    return '''<div class="newsItem"><div class="date">2025-01-13</div>
    <h3 class="title"><a href="https://www.tymetals.com.tw/news-detail/123/">豐興鋼鐵114.01.13開盤資訊</a></h3>
    <p>DO NOT STORE FULL ARTICLE OR SUMMARY</p></div>'''.encode()


def test_only_factual_metadata_and_unknown_impact():
    rows=ns.parse(html());assert len(rows)==1
    assert rows[0]['countries']==['台灣'] and rows[0]['impact']=='unknown'
    assert 'DO NOT STORE' not in str(rows)
    assert ns.classify('日本廢鋼進出口及關稅政策')['impact']=='unknown'
    assert '關稅與貿易政策' in ns.classify('日本廢鋼關稅政策')['categories']
    assert ns.classify('台灣廢鋼調降')['impact']=='down'
    with pytest.raises(ValueError):ns.parse(html().replace('豐興鋼鐵114.01.13開盤資訊'.encode(),'獨家深度新聞不可複製'.encode()))


def test_news_dedup_failure_preserves_titles_and_backup(monkeypatch):
    monkeypatch.setattr(ns,'fetch',lambda url,audit:b'User-agent: *\nAllow: /' if url.endswith('robots.txt') else html())
    with TestClient(app.app) as c:
        assert c.post('/api/news/update').json()['status']['added']==1
        state=ns.state();assert c.post('/api/news/update').json()['skipped']
        assert ns.state()==state
        state['checked_at']='2000-01-01T00:00:00+00:00'
        with app.connect() as db:db.execute("UPDATE market_settings SET value=? WHERE name='news_status'",(__import__('json').dumps(state),))
        monkeypatch.setattr(ns,'fetch',lambda url,audit:(_ for _ in ()).throw(SourceError(url,'TEST HTTP403',403)))
        assert not c.post('/api/news/update').json()['ok']
        assert len(c.get('/api/news').json()['items'])==1
        backup=c.get('/api/portable-backup').content
        assert c.post('/api/restore',files={'file':('backup.zip',backup)}).status_code==200
        assert len(c.get('/api/news').json()['items'])==1
