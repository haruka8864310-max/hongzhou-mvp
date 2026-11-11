import re
import pytest
from fastapi.testclient import TestClient
import app,china_sources as cn,cross_history as history,metal_engine as engine
from test_china_sources import release,URL


def test_older_visible_header_not_migration_url_date():
    raw=release().decode()
    raw=re.sub('<meta[^>]+>','',raw)
    raw='<h1>2025年1月上旬流通领域重要生产资料市场价格变动情况</h1><div class="detail-title-des"><h2><p>2025/01/15 09:30</p></h2></div>'+raw
    p=cn.parse(raw.encode(),URL)
    assert p['published_at']=='2025-01-15T01:30:00+00:00'
    with pytest.raises(ValueError):cn.parse(raw.replace('09:30','未知').encode(),URL)
    with pytest.raises(ValueError):cn.parse(raw.replace('Φ20mm','Φ16-25mm').encode(),URL)


def test_archive_idempotent_original_acquisition_and_unit():
    with TestClient(app.app):
        assert history.store_release(release(),URL)==(1,1)
        assert history.store_release(release(),URL)==(0,1)
        with app.connect() as c:r=c.execute("SELECT * FROM global_quotes WHERE origin LIKE 'archive-nbs|%'").fetchone()
        assert r['first_seen_at']>r['published_at'] and r['currency']=='CNY' and r['unit']=='t'
        assert history.inventory()['series'][0]['missing_publication_ratio']==0


def test_official_document_urls_are_one_fixed_scope_history():
    import scrap_analysis as sa
    with TestClient(app.app):
        history.store_release(release(),URL)
        other=release().replace('上旬'.encode(),'中旬'.encode()).replace(b'2025/01/15',b'2025/01/25')
        history.store_release(other,URL.replace('1234567','1234568'))
        series=[s for s in sa.datasets().values() if s['market']=='cn_rebar']
        assert len(series)==1 and len(series[0]['points'])==2
        assert len({p['source_url'] for p in series[0]['points']})==2
        # The public replay adapter must not backdate local acquisition clocks.
        assert not [s for s in sa.datasets('2025-02-01T00:00:00+00:00').values() if s['market']=='cn_rebar']


def test_backfill_does_not_downgrade_latest_status():
    with TestClient(app.app):
        meta=engine.series_meta('bls_steel_scrap','TEST index','bls','INDEX','index','TEST')
        engine.store('bls',[(meta,[('2025-01',100,'')])],None)
        engine.store('bls',[(meta,[('1997-01',80,'')])],None)
        with app.connect() as c:assert c.execute("SELECT latest_period FROM metal_updates WHERE provider='bls'").fetchone()[0]=='2025-01'
