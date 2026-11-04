import io,json
from fastapi.testclient import TestClient
import app,metal_engine as engine,metal_analysis as analysis
from test_metals import seed,workbook


def load_steel():
    for provider,name in [('tw_rebar','鋼筋'),('tw_hbeam','H型鋼'),('tw_plate','鋼板')]:
        specs=['SD280，熱軋，D10mm','SD280，熱軋，D16mm'] if provider=='tw_rebar' else ['固定A','固定B']
        raw='調查時間,調查項目,單位,北區,中區\n'+''.join(f'11404,產品，{name}，{spec},T,100,110\n' for spec in specs)
        parsed,modified=engine.parse_taiwan(raw.encode(),'tw_rebar' if provider=='tw_rebar' else provider)
        engine.store(provider,parsed,modified)


def test_representatives_fixed_and_saved():
    with TestClient(app.app) as c:
        load_steel();seed()
        rows=c.get('/api/metals/series').json();reps=c.get('/api/metals/representatives').json()
        selected=next(s for s in rows if s['id']==reps['tw_rebar'])
        assert 'D16mm' in selected['specification'] and '北區' in selected['specification']
        assert sum(s['representative'] for s in rows if s['provider'].startswith('tw_'))==3
        replacement=next(s['id'] for s in rows if s['provider']=='tw_rebar' and s['id']!=reps['tw_rebar'])
        reps['tw_rebar']=replacement
        assert c.put('/api/metals/representatives',json=reps).status_code==200
        assert c.put('/api/metals/representatives',json={**reps,'tw_plate':replacement}).status_code==400
        backup=c.get('/api/portable-backup').content
    with TestClient(app.app) as c:
        assert c.get('/api/metals/representatives').json()==reps
        assert c.post('/api/restore',files={'file':('backup.zip',backup)}).status_code==200
        assert c.get('/api/metals/representatives').json()==reps
        points=c.get('/api/metals/highlights').json()['items']
        assert next(p for p in points if p['label']=='銅')['change']
        assert next(p for p in points if p['id']==replacement)['change'] is None


def test_official_and_normalized_uploads_atomic():
    with TestClient(app.app) as c:
        result=c.post('/api/metals/import-file',data={'provider':'worldbank'},files={'file':('official.xlsx',workbook())})
        assert result.status_code==200
        before=len(engine.observations('wb_copper'))
        raw='series_id,period,value,source_url\nwb_copper,2024-01,50,https://wrong.example/\n'
        assert c.post('/api/metals/import-file',data={'provider':'worldbank'},files={'file':('bad.csv',raw)}).status_code==400
        assert len(engine.observations('wb_copper'))==before
        raw=f'series_id,period,value,source_url\nwb_copper,2024-01,50,{engine.WB_URL}\n'
        assert c.post('/api/metals/import-file',data={'provider':'worldbank'},files={'file':('valid.csv',raw)}).status_code==200
        assert next(p for p in engine.observations('wb_copper') if p['period']=='2024-01')['value']==50
        assert c.post('/api/metals/import-file',data={'provider':'worldbank'},files={'file':('bad.xlsx',b'not an excel')}).status_code==400
        from openpyxl import Workbook
        book=Workbook();sheet=book.active;sheet.append(['series_id','period','value','source_url']);sheet.append(['wb_copper','2024-02',55,engine.WB_URL]);output=io.BytesIO();book.save(output);book.close()
        assert c.post('/api/metals/import-file',data={'provider':'worldbank'},files={'file':('normalized.xlsx',output.getvalue())}).status_code==200
        load_steel();s=next(s for s in engine.all_series() if s['provider']=='tw_rebar')
        raw=f"series_id,period,value,source_url\n{s['id']},2024-01,99,{s['source_url']}\n"
        assert c.post('/api/metals/import-file',data={'provider':'tw_rebar'},files={'file':('normalized.csv',raw)}).status_code==200


def test_bls_ferrous_distinct_from_copper():
    data={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':code,'data':[{'year':'2025','period':'M01','value':'123'}]} for code in ('WPU1012','WPU102301')]}}
    parsed,_=engine.parse_bls(json.dumps(data).encode())
    assert {s['id'] for s,_ in parsed}=={'bls_steel_scrap','bls_copper_scrap'}
    assert all(s['currency']=='INDEX' for s,_ in parsed)
