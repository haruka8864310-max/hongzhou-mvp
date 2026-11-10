"""Explicit real-network acceptance, always in a new disposable data directory."""
import json,os,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
(root/'.test-runs').mkdir(exist_ok=True)
data=Path(tempfile.mkdtemp(prefix='live-metals-',dir=root/'.test-runs'))
os.environ['HONGZHOU_DATA_DIR']=str(data);os.environ['HONGZHOU_DISABLE_SCHEDULER']='1'
sys.path.insert(0,str(root))
import app
from fastapi.testclient import TestClient
with TestClient(app.app) as c:
    update=c.post('/api/metals/sync').json()
    series=c.get('/api/metals/series').json()
    wb=[s for s in series if s['provider']=='worldbank']
    assert len(wb)==4,update
    trend=c.get('/api/metals/trend',params={'series':'wb_copper','months':120}).json()
    query={'a':'wb_copper','b':'wb_aluminum','months':120,'mode':'index'}
    compare=c.post('/api/metals/compare',json=query).json()
    leading=c.post('/api/metals/leading',json=query).json()
    radar=c.get('/api/metals/radar').json()
    assert len(compare['points'])==120 and trend['changes']['12']
    fx_settings=c.get('/api/market/settings').json();fx_settings['analysis_days']=1825
    c.put('/api/market/settings',json=fx_settings)
    fx=c.post('/api/market/sync').json()
    twd=c.post('/api/metals/compare',json={**query,'mode':'twd'}).json()
    captured=c.post('/api/global/capture').json()
    global_overview=c.get('/api/global/overview').json()
    global_copper=next(r for r in global_overview['items'] if r['id']=='global_copper')
    global_id=global_copper['series'][0]['id']
    global_history=c.get('/api/global/history',params={'series':global_id,'months':36}).json()
    assert len(global_history['points'])==36
    summary={'data_directory':str(data),'updates':update,'series_count':len(series),
       'series':[{'id':s['id'],'unit':s['unit'],'count':s['count'],'first':s['first_period'],'latest':s['latest']['period']} for s in series],
       'comparison_months':len(compare['points']),'twd_months':len(twd['points']),'leading_samples':[r['samples'] for r in leading['results']],
       'radar_signals':len(radar['signals']),'fx_ok':fx['ok'],'has_month_change':trend['changes']['1'] is not None,
       'global_versions_added':captured['versions_added'],'global_copper_months':len(global_history['points'])}
    (data/'acceptance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**summary,'series':[s for s in summary['series'] if s['id'].startswith(('wb_','bls_'))]},ensure_ascii=True,indent=2))
