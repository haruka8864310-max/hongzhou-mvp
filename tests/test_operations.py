"""Independent DB only. Monetary examples below are synthetic test fixtures."""
import io,sqlite3
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import app,operations as ops,update_manager as updates

@pytest.fixture
def client():
    with TestClient(app.app) as c:yield c

def customer(c,name='TEST supplier'):
    r=c.post('/api/operations/customers',json={'name':name,'tax_id':'12345678' if name=='TEST supplier' else ''});assert r.status_code==200,r.text
    return r.json()

def buy(c,key,**extra):
    data=dict(customer_key=key,date='2026-11-01',kind='buy',material='TEST steel',gross_kg='1010',deduction_kg='10',unit_price='10',transport='600',processing='400')
    return c.post('/api/operations/trades',json={**data,**extra})

def sale(c,key,purchase_id,kg='400',**extra):
    data=dict(customer_key=key,date='2026-11-02',kind='sell',material='TEST steel',gross_kg=kg,unit_price='15',transport='100',processing='100',purchase_id=purchase_id)
    return c.post('/api/operations/trades',json={**data,**extra})

def test_customer_permanent_key_duplicates_disable_and_search(client):
    c=customer(client);key=c['customer_key'];assert len(key)==32
    assert client.post('/api/operations/customers',json={'name':' test SUPPLIER '}).status_code==409
    assert client.post('/api/operations/customers',json={'name':'Different','tax_id':'12345678'}).status_code==409
    r=client.put('/api/operations/customers/'+key,json={'name':'TEST renamed','phone':'0912345','active':False}).json()
    assert r['customer_key']==key
    assert client.get('/api/operations/customers?q=0912&active=all').json()[0]['customer_key']==key
    assert client.get('/api/operations/customers').json()==[]
    assert buy(client,key).status_code==409
    app.setup_database()
    assert client.get('/api/operations/customers/'+key).json()['customer']['customer_key']==key

def test_partial_shipments_realized_profit_and_overselling(client):
    c=customer(client);key=c['customer_key'];b=buy(client,key).json()
    assert b['kg']==1000 and b['amount']==10000 and b['cost_per_tonne']==11000
    s=sale(client,key,b['id']);assert s.status_code==200,s.text
    assert s.json()['allocated_cost']==4400 and s.json()['realized_profit']==1400
    assert s.json()['cost_per_tonne']==11500 and s.json()['total_cost']==4600
    r=client.get('/api/operations/profit?sale_price=15&estimated_sale_cost=200').json()
    assert r['realized_profit']==1400 and r['unallocated_cost']==6600 and r['estimated_profit']==2200
    assert sale(client,key,b['id'],'601').status_code==409
    assert sale(client,key,b['id'],'10',date='2026-10-31').status_code==422
    assert sale(client,key,b['id'],'10',material='TEST copper').status_code==422
    s2=sale(client,key,b['id'],'600',date='2026-11-03').json()
    assert s2['realized_profit']==2200
    r=client.get('/api/operations/profit?start=2026-11-03&end=2026-11-03').json()
    assert r['realized_profit']==2200 and r['unallocated_cost']==0
    # Earlier as-of report must ignore the later second shipment.
    r=client.get('/api/operations/profit?end=2026-11-02').json()
    assert r['realized_profit']==1400 and r['unallocated_cost']==6600
    detail=client.get('/api/operations/customers/'+key).json()
    assert len(detail['trades'])==3 and detail['totals']['buy']['kg']==1000
    assert client.get('/api/operations/trades?start=2026-11-05&end=2026-11-01').status_code==422

def test_trade_edits_lock_cost_export_and_legacy_preservation(client):
    key=customer(client)['customer_key'];b=buy(client,key).json();s=sale(client,key,b['id']).json()
    base=dict(customer_key=key,date='2026-11-01',kind='buy',material='TEST steel',gross_kg=1010,deduction_kg=10,unit_price=11,transport=600,processing=400)
    assert client.put('/api/operations/trades/'+str(b['id']),json=base).status_code==409
    assert client.put('/api/operations/trades/'+str(b['id']),json={**base,'unit_price':10,'payment_status':'paid','note':'TEST note'}).status_code==200
    assert client.put('/api/operations/trades/'+str(s['id']),json=dict(customer_key=key,date='2026-11-02',kind='sell',material='TEST steel',gross_kg=200,unit_price=15,purchase_id=b['id'])).status_code==200
    x=client.get('/api/operations/export?kind=sell');assert x.status_code==200
    assert load_workbook(io.BytesIO(x.content)).active.max_row==2
    with app.connect() as c:
        c.execute("INSERT INTO trades(date,kind,material,kg,unit_price) VALUES('2025-01-01','sell','OLD',5,10)")
        before=c.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    app.setup_database()
    assert len(client.get('/api/trades').json())==before
    assert client.get('/api/operations/profit').json()['legacy_unallocated']==1

def test_startup_no_network_and_separate_routes(client,monkeypatch):
    import market_sources,global_market
    monkeypatch.setattr(market_sources,'start_scheduler',lambda:pytest.fail('startup launched scheduler'))
    monkeypatch.setattr(global_market,'capture_existing',lambda:pytest.fail('startup reimported history'))
    with TestClient(app.app) as c:
        for route in ('/','/customers','/purchases','/shipments','/profits','/fenghsin','/international','/analysis','/updates','/prediction-lab'):
            r=c.get(route);assert r.status_code==200 and 'id="sideNav"' in r.text
        assert 'customerForm' not in c.get('/').text and 'tradeForm' not in c.get('/customers').text
        assert 'fhChart' not in c.get('/profits').text
        assert c.get('/api/updates').json()['manual_only']

def test_explicit_update_failure_keeps_cache(client,monkeypatch):
    import time
    monkeypatch.setattr(updates,'fetch',lambda key:(_ for _ in ()).throw(RuntimeError('TEST network offline')))
    with app.connect() as c:before=c.execute('SELECT COUNT(*) FROM global_quotes').fetchone()[0]
    assert client.post('/api/updates/china').status_code==200
    for _ in range(100):
        r=next(r for r in client.get('/api/updates').json()['sources'] if r['source']=='china')
        if r['state']!='running':break
        time.sleep(.01)
    assert r['state']=='failed' and 'offline' in r['error']
    with app.connect() as c:assert c.execute('SELECT COUNT(*) FROM global_quotes').fetchone()[0]==before

def test_skipped_update_does_not_claim_new_download_time(client,monkeypatch):
    import time
    stamp='2026-11-01T01:00:00+00:00'
    monkeypatch.setattr(updates,'fetch',lambda key:{'ok':True,'skipped':True,'message':'TEST cached','status':{'success_at':stamp}})
    assert client.post('/api/updates/china').status_code==200
    for _ in range(100):
        r=next(r for r in client.get('/api/updates').json()['sources'] if r['source']=='china')
        if r['state']!='running':break
        time.sleep(.01)
    assert r['state']=='cached' and r['success_at']==stamp and r['added']==0

def test_legacy_migration_retains_values_and_backs_up(tmp_path,monkeypatch):
    root=tmp_path/'legacy';root.mkdir();db=root/'hongzhou.db'
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,address TEXT DEFAULT '',phone TEXT DEFAULT '',note TEXT DEFAULT ''); CREATE TABLE trades(id INTEGER PRIMARY KEY,date TEXT NOT NULL,customer_id INTEGER,kind TEXT NOT NULL,material TEXT NOT NULL,kg REAL NOT NULL,unit_price REAL NOT NULL,transport REAL DEFAULT 0,note TEXT DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP); INSERT INTO customers(id,name) VALUES(9,'TEST preserved'); INSERT INTO trades(id,date,customer_id,kind,material,kg,unit_price) VALUES(5,'2025-01-01',9,'buy','TEST',100,10);")
    monkeypatch.setattr(app,'ROOT',root);monkeypatch.setattr(app,'DB',db);monkeypatch.setattr(app,'BACKUPS',root/'backups');app.setup_database()
    with app.connect() as c:
        assert c.execute('SELECT name FROM customers WHERE id=9').fetchone()[0]=='TEST preserved'
        assert c.execute('SELECT kg FROM trades WHERE id=5').fetchone()[0]==100
    assert list(app.BACKUPS.glob('*.db'))

def test_new_operations_backup_round_trip_and_concurrent_capacity(client):
    from concurrent.futures import ThreadPoolExecutor
    key=customer(client)['customer_key'];b=buy(client,key).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes=list(pool.map(lambda _:sale(client,key,b['id'],'600').status_code,range(2)))
    assert sorted(codes)==[200,409]
    snapshot=client.get('/api/portable-backup').content
    assert client.put('/api/operations/customers/'+key,json={'name':'TEST changed'}).status_code==200
    assert client.post('/api/restore',files={'file':('operations.zip',snapshot)}).status_code==200
    detail=client.get('/api/operations/customers/'+key).json()
    assert detail['customer']['name']=='TEST supplier' and len(detail['trades'])==2
    assert client.get('/api/operations/profit').json()['realized_profit']==2200

def test_existing_trade_api_supports_explicit_purchase_link(client):
    c=customer(client);b=client.post('/api/trades',json={'date':'2026-11-01','customer_id':c['id'],'kind':'buy','material':'TEST','kg':100,'unit_price':10}).json()
    data={'date':'2026-11-02','customer_id':c['id'],'kind':'sell','material':'TEST','kg':50,'unit_price':15}
    assert client.post('/api/trades',json=data).status_code==400
    r=client.post('/api/trades',json={**data,'purchase_id':b['id']})
    assert r.status_code==200 and r.json()['realized_profit']==250

def test_partial_cost_allocations_preserve_last_cent(client):
    key=customer(client)['customer_key'];b=buy(client,key,gross_kg='3',deduction_kg='0',unit_price='0.0033',transport='0',processing='0').json()
    assert b['total_cost']==.01
    sales=[sale(client,key,b['id'],'1',unit_price='1',transport='0',processing='0').json() for _ in range(3)]
    assert sum(ops.dec(s['allocated_cost']) for s in sales)==ops.dec('.01')
    result=client.get('/api/operations/profit').json()
    assert result['realized_profit']==2.99 and result['unallocated_cost']==0
