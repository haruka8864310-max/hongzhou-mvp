"""Synthetic fixtures only. Never load these into the production database."""
import sqlite3
import pytest
from fastapi.testclient import TestClient
import app
import fenghsin as fh


def table(source='juyi', revised=False):
    values = [['2025/1/13', '1200' if revised else '1100', '2200', '3300'],
              ['2025/1/6', '1000', '2100', '3300']]
    if source == 'double':
        values = [['2025/1/13', '上漲 100', '上漲 100', '平盤'],
                  ['2025/1/6', '平盤', '平盤', '平盤']]
    return ('<table><tr><th>公布日期</th><th>廢鋼收購</th><th>鋼筋</th><th>型鋼</th></tr>' +
            ''.join('<tr>'+''.join('<td>'+v+'</td>' for v in row)+'<td>DO NOT STORE ARTICLE</td></tr>' for row in values)+'</table>').encode()


def network(monkeypatch, revised=False):
    def fetch(url):
        if url.endswith('robots.txt'):
            return b'User-agent: *\nAllow: /'
        return table('juyi' if url == fh.SOURCES['juyi'][1] else 'double', revised)
    monkeypatch.setattr(fh, 'fetch', fetch)


def expire():
    with app.connect() as c:
        c.execute("UPDATE fenghsin_source_status SET checked_at='2000-01-01T00:00:00+00:00'")


def test_parse_strict_dates_columns_and_facts_only():
    rows = fh.parse('juyi', table())
    assert len(rows) == 6 and all('ARTICLE' not in str(r) for r in rows)
    assert rows[0]['price'] == 1100 and rows[0]['published_at'] is None
    assert all(r['verification'] == 'unverified' for r in rows)
    assert fh.parse('double', table('double'))[2]['adjustment'] == 0
    for old, new in [(b'2025/1/13', b'2999/1/13'), (b'1100', b'flat'), (b'2025/1/6', b'2025/1/13')]:
        with pytest.raises(ValueError):
            fh.parse('juyi', table().replace(old, new))
    with pytest.raises(ValueError):
        fh.parse('double', table('double').replace('上漲 100'.encode(), '洽詢'.encode()))


def test_end_to_end_dedup_revisions_failure_persistence_backup(monkeypatch):
    network(monkeypatch)
    with TestClient(app.app) as c:
        assert 'fhChart' in c.get('/').text
        assert c.post('/api/fenghsin/sync').json()['ok']
        history = c.get('/api/fenghsin/history').json()
        assert history['stats']['count'] == 2 and history['stats']['change'] == 100
        assert history['points'][-1]['changes'][0]['adjustment'] == 100
        assert history['checks'][0]['consistent']
        assert history['backfills'] == []
        assert c.get('/api/fenghsin/history?product=bad').status_code == 422
        assert c.get('/api/fenghsin/history?months=-1').status_code == 422
        expire()
        assert c.post('/api/fenghsin/sync').json()['ok']
        assert len(fh.facts()) == 12
        network(monkeypatch, revised=True)
        expire()
        assert c.post('/api/fenghsin/sync').json()['ok']
        assert len(fh.facts()) == 13
        assert c.get('/api/fenghsin/history').json()['stats']['count'] == 1
        assert len(c.get('/api/fenghsin/history').json()['conflicts']) == 1
        expire()
        monkeypatch.setattr(fh, 'fetch', lambda _: (_ for _ in ()).throw(OSError('TEST offline')))
        assert not c.post('/api/fenghsin/sync').json()['ok']
        assert len(fh.facts()) == 13
        states = c.get('/api/fenghsin/overview').json()['sources']
        assert all(s['status']['success_at'] and 'offline' in s['status']['error'] for s in states)
        backup = c.get('/api/portable-backup').content
        assert c.post('/api/restore', files={'file': ('backup.zip', backup)}).status_code == 200
        assert len(fh.facts()) == 13
    with TestClient(app.app) as c:
        assert c.get('/api/fenghsin/overview').json()['items'][0]['conflicts'] == 1
        assert len(fh.facts()) == 13


def test_robots_denial_never_downloads_prices(monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return b'User-agent: *\nDisallow: /'
    monkeypatch.setattr(fh, 'fetch', fetch)
    with TestClient(app.app) as c:
        assert not c.post('/api/fenghsin/sync').json()['ok']
        assert len(calls) == 2 and all(u.endswith('robots.txt') for u in calls)
        assert fh.facts() == []
        assert c.get('/api/fenghsin/history').json()['stats']['count'] == 0


def event(day, price=None, adjustment=0, **kw):
    return {'id': day, 'announcement_date': '2025-01-'+day, 'product': 'scrap',
            'grade': 'TEST grade', 'tax_basis': 'TEST tax', 'price_basis': 'TEST purchase',
            'price': price, 'adjustment': adjustment, 'verification': 'cross_validated', **kw}


def test_reconstruction_stops_at_gaps_scope_conflict_or_missing_change():
    anchor = event('27', 1500, 100)
    result, reason = fh.reconstruct([event('20', None, 0), event('13', None)], anchor)
    assert [r['price'] for r in result] == [1400, 1400]
    assert result[0]['kind'] == 'backfilled' and result[0]['anchor_id'] == '27'
    assert result[1]['parent_ids'] == ['20', '13']
    for previous, message in [(event('13'), '超過7日'), (event('20', grade='OTHER'), '口徑不同'),
                              (event('20', 1200), '矛盾')]:
        rows, reason = fh.reconstruct([previous], anchor)
        assert rows == [] and message in reason
    assert '明確調價' in fh.reconstruct([event('20')], event('27', 1500, None))[1]
    assert '獨立驗證' in fh.reconstruct([event('20')], event('27', 1500, verification='unverified'))[1]
    assert '多版本' in fh.reconstruct([event('20'), event('20', adjustment=100)], anchor)[1]


def test_v5_upgrade_backs_up_before_additive_tables():
    app.setup_database()
    with app.connect() as c:
        c.execute('DELETE FROM schema_migrations WHERE version=6')
        c.execute('DROP TABLE fenghsin_facts')
        c.execute('DROP TABLE fenghsin_source_status')
        c.execute("INSERT INTO customers(name) VALUES('TEST preserved')")
    app.setup_database()
    with app.connect() as c:
        assert c.execute('SELECT name FROM customers').fetchone()[0] == 'TEST preserved'
    backups = sorted(app.BACKUPS.glob('*.db'))
    assert backups
    with sqlite3.connect(backups[-1]) as c:
        assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 5
