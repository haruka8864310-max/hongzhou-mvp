"""Download numeric facts into a NEW disposable DB, verify SQL and restart.

No real prices or downloaded articles are embedded in repository fixtures.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

project = Path(__file__).resolve().parents[1]
(project / '.test-runs').mkdir(exist_ok=True)
data = Path(tempfile.mkdtemp(prefix='live-fenghsin-', dir=project / '.test-runs'))
os.environ['HONGZHOU_DATA_DIR'] = str(data)
os.environ['HONGZHOU_DISABLE_SCHEDULER'] = '1'
sys.path.insert(0, str(project))
import app
from fastapi.testclient import TestClient

with TestClient(app.app) as client:
    result = client.post('/api/fenghsin/sync').json()
    summary = {'data_directory': str(data), 'update': result, 'products': []}
    for product in ('scrap', 'rebar', 'sections'):
        history = client.get('/api/fenghsin/history', params={'product': product}).json()
        points = history['points']
        assert len(points) >= 2, result
        assert all(p['price'] > 0 and p['source_url'].startswith('https://') for p in points)
        summary['products'].append({'product': product, 'count': len(points),
                                    'first': points[0]['date'], 'latest': points[-1]['date']})
    with app.connect() as c:
        summary['sqlite'] = [dict(r) for r in c.execute('''SELECT source,kind,COUNT(*) records,
            MIN(announcement_date) earliest,MAX(announcement_date) latest
            FROM fenghsin_facts GROUP BY source,kind''')]
    assert client.get('/api/portable-backup').status_code == 200
with TestClient(app.app) as client:
    overview = client.get('/api/fenghsin/overview').json()
    assert [r['count'] for r in overview['items']] == [p['count'] for p in summary['products']]
    summary['restart_persisted'] = True
(data / 'acceptance.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True, indent=2))
