"""Explicit live-source smoke test. Writes only to an isolated ignored test directory.

Run: .venv\Scripts\python scripts/check_live_rates.py
No synthetic data, production database access or outbound customer data.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

project = Path(__file__).resolve().parents[1]
test_root = project / '.test-runs'
test_root.mkdir(exist_ok=True)
data_root = Path(tempfile.mkdtemp(prefix='live-rates-',dir=test_root))
os.environ['HONGZHOU_DATA_DIR'] = str(data_root)
os.environ['HONGZHOU_DISABLE_SCHEDULER'] = '1'
sys.path.insert(0,str(project))

import app
import market_sources
from fastapi.testclient import TestClient

with TestClient(app.app) as client:
    result=client.post('/api/market/sync').json()
    overview=client.get('/api/market/overview').json()
    sources=client.get('/api/market/sources').json()
    summary={'data_directory':str(data_root),'sync':result,
        'quotes':[{'instrument':s['latest']['instrument'],'date':s['latest']['date'],
                   'price':s['latest']['price'],'count':s['count'],'currency':s['latest']['currency'],
                   'unit':s['latest']['unit'],'publication_known':s['latest']['publication_known']} for s in overview['series']],
        'sources':[s for s in sources['sources'] if s['automatic']]}
    if result['ok']:
        assert len(overview['series'])==3
        assert all(s['count']>=30 for s in overview['series'])
        keys=[s['key'] for s in overview['series'][:2]]
        from datetime import datetime,timedelta
        end=datetime.now(market_sources.TAIPEI).date()
        comparison=client.post('/api/market/compare',json={'series':keys,'start':(end-timedelta(days=365)).isoformat(),'end':end.isoformat(),'mode':'index'}).json()
        summary['aligned_dates']=len(comparison['points'])
        assert len(comparison['points'])>=30
    (data_root/'live-result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True,indent=2))
    if not result['ok']:
        raise SystemExit(1)
