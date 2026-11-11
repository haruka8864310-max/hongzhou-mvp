from fastapi.testclient import TestClient
import pytest
import app,china_sources as cn,global_market as gm
from public_fetch import SourceError

URL='https://www.stats.gov.cn/sj/zxfb/202501/t20250115_1234567.html'


def release(price='2100',change='100',percent='5.0'):
    # Synthetic official-format fixture, not real prices.
    return ('''<meta name="ArticleTitle" content="2025年1月上旬流通领域重要生产资料市场价格变动情况">
    <meta name="PubDate" content="2025/01/15 09:30"><table><tr><td>螺纹钢（Φ20mm，HRB400E）</td><td>吨</td>
    <td>'''+price+'</td><td>'+change+'</td><td>'+percent+'</td></tr></table>').encode()


def fixture(monkeypatch):
    def fetch(url,audit):
        audit.append({'url':url,'http_status':200,'error':None})
        if url.endswith('robots.txt'):return b'User-agent: *\nAllow: /'
        if url==cn.HOME:return ('<a href="'+URL+'">2025年1月上旬流通领域重要生产资料市场价格变动情况</a>').encode()
        return release()
    monkeypatch.setattr(cn,'fetch',fetch)


def test_parser_duplicate_layout_and_reject_conflicting_or_wrong_scope():
    r=cn.parse(release(),URL)
    assert r['quote_date']=='2025-01-10' and r['period_start']=='2025-01-01'
    assert r['published_at']=='2025-01-15T01:30:00+00:00'
    assert cn.parse(release()+release(),URL)['price']==2100
    for raw in [release()+release('2200','100','4.8'),release().replace(b'HRB400E',b'OTHER'),release(percent='90')]:
        with pytest.raises(ValueError):cn.parse(raw,URL)


def test_download_sql_dedup_failure_and_restart(monkeypatch):
    fixture(monkeypatch)
    with TestClient(app.app) as c:
        r=c.post('/api/china/update').json();assert r['ok'] and r['status']['new_versions']==1
        row=next(r for r in gm.available() if r['market']=='cn_rebar')
        assert row['currency']=='CNY' and row['frequency']=='observed' and row['document_url']==URL
        assert '旬均期間' in row['license_note'] and '非廢鋼' in row['delivery_term']
        prior=cn.state();assert c.post('/api/china/update').json()['skipped']
        assert cn.state()==prior
        prior['checked_at']='2000-01-01T00:00:00+00:00';cn.save(prior)
        assert c.post('/api/china/update').json()['status']['new_versions']==0
        prior=cn.state();prior['checked_at']='2000-01-01T00:00:00+00:00';cn.save(prior)
        monkeypatch.setattr(cn,'fetch',lambda url,audit:(_ for _ in ()).throw(SourceError(url,'HTTP403 TEST',403)))
        assert not c.post('/api/china/update').json()['ok']
        assert len(gm.available())==1 and cn.state()['success_at']
        failed=cn.state();c.post('/api/china/update');assert cn.state()==failed
    with TestClient(app.app):assert len(gm.available())==1
