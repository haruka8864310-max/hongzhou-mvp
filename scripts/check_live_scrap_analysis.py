"""Real network to a fresh isolated SQLite, never company data or fake quotes."""
import json
import os
import sys
import tempfile
from pathlib import Path

project=Path(__file__).resolve().parents[1]
(project/'.test-runs').mkdir(exist_ok=True)
data=Path(tempfile.mkdtemp(prefix='live-scrap-analysis-',dir=project/'.test-runs'))
os.environ['HONGZHOU_DATA_DIR']=str(data)
os.environ['HONGZHOU_DISABLE_SCHEDULER']='1'
sys.path.insert(0,str(project))
import app
from fastapi.testclient import TestClient

with TestClient(app.app) as c:
    updates={'fenghsin':c.post('/api/fenghsin/sync').json(),
             'tokyo':c.post('/api/japan/update').json(),
             'fx':c.post('/api/market/sync').json()}
    c.post('/api/global/capture')
    defaults=c.get('/api/scrap-analysis/catalog').json()['defaults']
    assert defaults['a'] and defaults['b'],updates
    modes={}
    for mode in ('native','index','twd'):
        r=c.get('/api/scrap-analysis/compare',params={'mode':mode,'months':0}).json()
        modes[mode]={k:len(v) for k,v in r['curves'].items()}
        assert all(n>=2 for n in modes[mode].values()),r
        assert all(p['source_url'] for ps in r['curves'].values() for p in ps)
    research=c.get('/api/scrap-analysis/research',params={'months':0}).json()
    assert len(research['leading'])==len(research['forecasts'])==4
    assert all(f['probabilities'] is None for f in research['forecasts'])
    assert c.get('/api/portable-backup').status_code==200
    with app.connect() as con:
        counts={'fenghsin':con.execute('SELECT COUNT(*) FROM fenghsin_facts').fetchone()[0],
                'tokyo':con.execute("SELECT COUNT(*) FROM global_quotes WHERE market='jp_tokyo'").fetchone()[0]}
    report={'data_directory':str(data),'updates':updates,'modes':modes,'sqlite':counts,
            'lead_samples':[r['samples'] for r in research['leading']],
            'forecasts':[{'weeks':r['weeks'],'training_samples':r['training_samples'],
                          'backtest_samples':r['backtest_samples'],'probabilities':r['probabilities']} for r in research['forecasts']]}
with TestClient(app.app) as c:
    result=c.get('/api/scrap-analysis/compare',params={'mode':'native','months':0}).json()
    assert {k:len(v) for k,v in result['curves'].items()}==modes['native']
    report['restart_persisted']=True
(data/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))
