"""Real-source acceptance. Always creates a new isolated DB; never company data."""
import json,os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];(ROOT/'.test-runs').mkdir(exist_ok=True)
DATA=Path(tempfile.mkdtemp(prefix='live-japan-',dir=ROOT/'.test-runs'))
os.environ['HONGZHOU_DATA_DIR']=str(DATA);os.environ['HONGZHOU_DISABLE_SCHEDULER']='1';sys.path.insert(0,str(ROOT))
import app
from fastapi.testclient import TestClient
with TestClient(app.app) as client:
    japan=client.post('/api/japan/update').json()
    monthly=client.post('/api/metals/sync').json()
    fx=client.post('/api/market/sync').json()
    client.post('/api/global/capture')
    with app.connect() as c:
        ledger=[dict(r) for r in c.execute("SELECT market,COUNT(*) versions,COUNT(DISTINCT series_id || '|' || quote_date) observations,COUNT(DISTINCT series_id) series,MIN(quote_date) first,MAX(quote_date) latest FROM global_quotes GROUP BY market")]
        old=[dict(r) for r in c.execute('SELECT series_id,COUNT(*) observations,MIN(period) first,MAX(period) latest FROM metal_observations GROUP BY series_id')]
        samples=c.execute("SELECT series_id,COUNT(DISTINCT quote_date) n FROM global_quotes WHERE market='jp_tokyo' GROUP BY series_id ORDER BY n DESC").fetchall()
    if not samples:raise RuntimeError(json.dumps(japan,ensure_ascii=True))
    history=client.get('/api/global/history',params={'series':samples[0]['series_id'],'months':0}).json()
    assert len(history['points'])>=2 and history['latest']['currency']=='JPY'
    assert all(p['original_price']>0 for p in history['points'])
    backup=client.get('/api/portable-backup');assert backup.status_code==200
    summary={'data_directory':str(DATA),'tokyo':japan,'monthly':monthly,'fx':{'ok':fx['ok']},'sqlite_ledger':ledger,'sqlite_monthly':old,'japan_history_points':len(history['points']),'backup_http':backup.status_code,'jisri':client.get('/api/japan/status').json()['jisri']}
    (DATA/'acceptance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True,indent=2))
