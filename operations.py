"""Additive local operations ledger. Money is computed with Decimal, in TWD/kg.

A purchase is one immutable batch; partial shipments allocate its full landed
cost by net kg. Legacy unallocated trades are retained, never assigned invented
costs. No sales tax, financing or overhead assumptions are included.
"""
import io, json, re, uuid, unicodedata
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

router=APIRouter(prefix='/api/operations')

def connection():
    import app
    return app.connect()

def now(): return datetime.now(timezone.utc).isoformat()
def normalized(s): return re.sub(r'\s+', '', unicodedata.normalize('NFKC',s)).casefold()
def dec(v): return Decimal(str(v))
def money(v): return float(dec(v).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

def migrate(c):
    new_version=not c.execute('SELECT 1 FROM schema_migrations WHERE version=8').fetchone()
    additions={'customers':{'customer_key':'TEXT','tax_id':"TEXT NOT NULL DEFAULT ''",'contact':"TEXT NOT NULL DEFAULT ''",'customer_type':"TEXT NOT NULL DEFAULT '其他'",'main_items':"TEXT NOT NULL DEFAULT ''",'payment_terms':"TEXT NOT NULL DEFAULT ''",'tags':"TEXT NOT NULL DEFAULT ''",'active':'INTEGER NOT NULL DEFAULT 1','created_at':'TEXT','updated_at':'TEXT'},
      'trades':{'gross_kg':'REAL','deduction_kg':'REAL NOT NULL DEFAULT 0','processing':'REAL NOT NULL DEFAULT 0','payment_status':"TEXT NOT NULL DEFAULT 'unpaid'",'batch_key':'TEXT','purchase_id':'INTEGER REFERENCES trades(id)','flow_version':'INTEGER NOT NULL DEFAULT 0','updated_at':'TEXT'}}
    for table,fields in additions.items():
        present={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for key,definition in fields.items():
            if key not in present:c.execute(f'ALTER TABLE {table} ADD COLUMN {key} {definition}')
    for r in c.execute('SELECT id FROM customers WHERE customer_key IS NULL').fetchall():
        c.execute('UPDATE customers SET customer_key=?,created_at=COALESCE(created_at,?),updated_at=COALESCE(updated_at,?) WHERE id=?',(uuid.uuid4().hex,now(),now(),r[0]))
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS customer_permanent_key ON customers(customer_key)')
    c.execute('CREATE INDEX IF NOT EXISTS trade_customer_date ON trades(customer_id,date)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS purchase_batch_key ON trades(batch_key) WHERE kind=\'buy\' AND batch_key IS NOT NULL')
    c.execute('CREATE INDEX IF NOT EXISTS shipments_purchase ON trades(purchase_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS operations_audit(id INTEGER PRIMARY KEY,entity TEXT NOT NULL,entity_id INTEGER NOT NULL,before_json TEXT,after_json TEXT,changed_at TEXT NOT NULL)''')
    # Backfill old API inserts without changing existing customer IDs or trades.
    c.execute('''CREATE TRIGGER IF NOT EXISTS customer_key_on_insert AFTER INSERT ON customers WHEN NEW.customer_key IS NULL BEGIN UPDATE customers SET customer_key=lower(hex(randomblob(16))),created_at=COALESCE(NEW.created_at,CURRENT_TIMESTAMP),updated_at=COALESCE(NEW.updated_at,CURRENT_TIMESTAMP) WHERE id=NEW.id; END''')
    c.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(8)')
    if new_version:
        setting=c.execute("SELECT value FROM market_settings WHERE name='application'").fetchone()
        if setting:
            values=json.loads(setting[0]);values['auto_update']=False
            c.execute("UPDATE market_settings SET value=? WHERE name='application'",(json.dumps(values,ensure_ascii=False),))

def validate(c):
    c.execute('SELECT entity,entity_id,before_json,after_json,changed_at FROM operations_audit LIMIT 0')
    c.execute('SELECT source,state,started_at,success_at,error FROM operations_updates LIMIT 0')
    for r in c.execute('SELECT * FROM customers'):
        if not re.fullmatch('[a-f0-9]{32}',r['customer_key'] or ''):raise ValueError('客戶永久Key無效')
        CustomerInput(**{k:r[k] for k in CustomerInput.model_fields})
    seen=set()
    for r in c.execute('SELECT * FROM trades WHERE flow_version=1'):
        customer=c.execute('SELECT customer_key FROM customers WHERE id=?',(r['customer_id'],)).fetchone()
        if not customer:raise ValueError('交易缺少客戶')
        TradeInput(customer_key=customer[0],date=r['date'],kind=r['kind'],material=r['material'],gross_kg=r['gross_kg'],deduction_kg=r['deduction_kg'],unit_price=r['unit_price'],transport=r['transport'],processing=r['processing'],payment_status=r['payment_status'],purchase_id=r['purchase_id'],note=r['note'])
        if abs(dec(r['kg'])-(dec(r['gross_kg'])-dec(r['deduction_kg'])))>Decimal('.000001'):raise ValueError('交易淨重不符')
        if r['kind']=='buy':
            if not r['batch_key'] or r['batch_key'] in seen:raise ValueError('收購批次無效')
            seen.add(r['batch_key'])
            if dec(c.execute('SELECT COALESCE(SUM(kg),0) FROM trades WHERE purchase_id=?',(r['id'],)).fetchone()[0])>dec(r['kg'])+Decimal('.000001'):raise ValueError('批次出貨超重')
        else:
            p=c.execute("SELECT * FROM trades WHERE id=? AND kind='buy' AND flow_version=1",(r['purchase_id'],)).fetchone()
            if not p or p['batch_key']!=r['batch_key'] or p['material']!=r['material'] or p['date']>r['date']:raise ValueError('出貨批次關聯不符')

class CustomerInput(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    name:str=Field(min_length=1,max_length=150)
    tax_id:str=Field(default='',max_length=8,pattern=r'^(|[0-9]{8})$')
    contact:str=Field(default='',max_length=100)
    phone:str=Field(default='',max_length=80)
    address:str=Field(default='',max_length=300)
    customer_type:Literal['供應商','買方','兩者','其他']='其他'
    main_items:str=Field(default='',max_length=300)
    payment_terms:str=Field(default='',max_length=150)
    tags:str=Field(default='',max_length=300)
    note:str=Field(default='',max_length=3000)
    active:bool=True

def audit(c,entity,key,before,after):
    c.execute('INSERT INTO operations_audit(entity,entity_id,before_json,after_json,changed_at) VALUES(?,?,?,?,?)',(entity,key,json.dumps(before,ensure_ascii=False) if before else None,json.dumps(after,ensure_ascii=False),now()))

def customer_row(c,key):
    r=c.execute('SELECT * FROM customers WHERE customer_key=?',(key,)).fetchone()
    if not r:raise HTTPException(404,'客戶不存在')
    return dict(r)

def save_customer(p,key=None):
    with connection() as c:
        before=customer_row(c,key) if key else None
        for r in c.execute('SELECT id,name,tax_id FROM customers'):
            if before and r['id']==before['id']:continue
            if normalized(r['name'])==normalized(p.name) or (p.tax_id and r['tax_id']==p.tax_id):
                raise HTTPException(409,'已有相同名稱或統編的客戶（含停用客戶），請搜尋並編輯原客戶')
        values=p.model_dump();values['updated_at']=now()
        if before:
            c.execute('UPDATE customers SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),before['id']))
        else:
            key=uuid.uuid4().hex;values.update(customer_key=key,created_at=now())
            c.execute('INSERT INTO customers('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
        after=customer_row(c,key);audit(c,'customer',after['id'],before,after)
        return after

@router.get('/customers')
def customers(q:str=Query('',max_length=150),active:Literal['all','active','inactive']='active',customer_type:str=''):
    with connection() as c:rows=[dict(r) for r in c.execute('SELECT * FROM customers ORDER BY name,id')]
    return [r for r in rows if (active=='all' or bool(r['active'])==(active=='active')) and (not customer_type or r['customer_type']==customer_type) and (not q or normalized(q) in normalized(' '.join(str(r.get(k) or '') for k in ('name','tax_id','phone','contact','tags','main_items'))))]

@router.post('/customers')
def add_customer(p:CustomerInput):return save_customer(p)

@router.put('/customers/{key}')
def edit_customer(key:str,p:CustomerInput):return save_customer(p,key)

@router.get('/customers/{key}')
def detail_customer(key:str):
    with connection() as c:
        customer=customer_row(c,key)
        rows=trade_rows(c,customer_id=customer['id'])
    totals={kind:{'kg':sum(r['kg'] for r in rows if r['kind']==kind),'amount':money(sum(dec(r['amount']) for r in rows if r['kind']==kind))} for kind in ('buy','sell')}
    for t in totals.values():t['average_price']=money(dec(t['amount'])/dec(t['kg'])) if t['kg'] else None
    # Per-material averages avoid pretending copper and steel are comparable.
    by_material=[]
    for material,kind in sorted({(r['material'],r['kind']) for r in rows}):
        subset=[r for r in rows if r['material']==material and r['kind']==kind];kg=sum(r['kg'] for r in subset);amount=sum(dec(r['amount']) for r in subset)
        by_material.append({'material':material,'kind':kind,'kg':kg,'amount':money(amount),'average_price':money(amount/dec(kg))})
    return {'customer':customer,'trades':rows,'totals':totals,'by_material':by_material}

class TradeInput(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid',allow_inf_nan=False)
    customer_key:str=Field(min_length=32,max_length=32)
    date:date
    kind:Literal['buy','sell']
    material:str=Field(min_length=1,max_length=80)
    gross_kg:Decimal=Field(gt=0,le=Decimal('100000000'),decimal_places=3)
    deduction_kg:Decimal=Field(default=Decimal(0),ge=0,decimal_places=3)
    unit_price:Decimal=Field(ge=0,le=Decimal('1000000'),decimal_places=4)
    transport:Decimal=Field(default=Decimal(0),ge=0,le=Decimal('100000000'),decimal_places=2)
    processing:Decimal=Field(default=Decimal(0),ge=0,le=Decimal('100000000'),decimal_places=2)
    payment_status:Literal['unpaid','partial','paid']='unpaid'
    purchase_id:int|None=Field(default=None,gt=0)
    note:str=Field(default='',max_length=3000)
    @model_validator(mode='after')
    def valid(self):
        if self.deduction_kg>=self.gross_kg:raise ValueError('扣重須小於毛重')
        if self.date>datetime.now(timezone(timedelta(hours=8))).date():raise ValueError('交易日期不能在未來')
        if self.kind=='sell' and not self.purchase_id:raise ValueError('出貨須選擇收購批次')
        if self.kind=='buy' and self.purchase_id:raise ValueError('收購不可指定其他收購單')
        return self

def trade_rows(c,kind=None,customer_id=None,start=None,end=None):
    where=[];args=[]
    for condition,value in [('t.kind=?',kind),('t.customer_id=?',customer_id),('t.date>=?',start),('t.date<=?',end)]:
        if value is not None:where.append(condition);args.append(str(value))
    rows=[dict(r) for r in c.execute('SELECT t.*,c.name customer,c.customer_key FROM trades t LEFT JOIN customers c ON c.id=t.customer_id'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY t.date DESC,t.id DESC',args)]
    purchases={r['id']:dict(r) for r in c.execute("SELECT * FROM trades WHERE kind='buy' AND flow_version=1")}
    used={r[0]:dec(r[1]) for r in c.execute("SELECT purchase_id,SUM(kg) FROM trades WHERE kind='sell' AND flow_version=1 GROUP BY purchase_id")}
    allocations={};cumulative={}
    for shipment in c.execute("SELECT id,purchase_id,kg FROM trades WHERE kind='sell' AND flow_version=1 ORDER BY date,id"):
        purchase=purchases.get(shipment['purchase_id'])
        if not purchase:continue
        net=dec(purchase['kg']);total=dec(money(net*dec(purchase['unit_price'])))+dec(purchase['transport'])+dec(purchase['processing'])
        previous=cumulative.get(purchase['id'],Decimal(0));current=previous+dec(shipment['kg'])
        # Cumulative cent allocation guarantees all partial shipments sum to
        # exactly the purchase cost, including the last cent of a small lot.
        allocations[shipment['id']]=dec(money(total*current/net))-dec(money(total*previous/net));cumulative[purchase['id']]=current
    for r in rows:
        kg=dec(r['kg']);r['amount']=money(kg*dec(r['unit_price']));r['total_cost']=money(dec(r['amount'])+dec(r['transport'])+dec(r['processing']))
        r['cost_per_tonne']=money(dec(r['total_cost'])/kg*1000)
        r['realized_profit']=None;r['allocated_cost']=None
        if r['kind']=='sell':r['total_cost']=None;r['cost_per_tonne']=None
        if r['kind']=='sell' and r['flow_version']==1 and r['purchase_id'] in purchases:
            cost=allocations[r['id']];r['allocated_cost']=money(cost);r['realized_profit']=money(dec(r['amount'])-cost-dec(r['transport'])-dec(r['processing']))
            r['total_cost']=money(cost+dec(r['transport'])+dec(r['processing']));r['cost_per_tonne']=money(dec(r['total_cost'])/kg*1000)
        if r['kind']=='buy' and r['flow_version']==1:
            sold=used.get(r['id'],Decimal(0));remaining=max(Decimal(0),kg-sold);r['remaining_kg']=float(remaining);r['unallocated_cost']=money(dec(r['total_cost'])-dec(money(dec(r['total_cost'])*sold/kg)))
    return rows

def save_trade(p,trade_id=None):
    with connection() as c:
        customer=customer_row(c,p.customer_key)
        before=None
        if trade_id:
            row=c.execute('SELECT * FROM trades WHERE id=?',(trade_id,)).fetchone()
            if not row:raise HTTPException(404,'交易不存在')
            before=dict(row)
            if before['flow_version']!=1:raise HTTPException(409,'舊交易缺少批次成本口徑，保留唯讀；請先核對原始單據')
            if before['kind']!=p.kind:raise HTTPException(409,'不可將收購改為出貨')
        if not customer['active'] and (not before or before['customer_id']!=customer['id']):raise HTTPException(409,'停用客戶不能新增交易')
        kg=p.gross_kg-p.deduction_kg
        if p.kind=='sell':
            row=c.execute("SELECT * FROM trades WHERE id=? AND kind='buy' AND flow_version=1",(p.purchase_id,)).fetchone()
            if not row:raise HTTPException(422,'找不到可分攤成本的收購批次')
            purchase=dict(row)
            if purchase['material']!=p.material:raise HTTPException(422,'品項必須與收購批次完全相同')
            if str(p.date)<purchase['date']:raise HTTPException(422,'出貨日不得早於收購日')
            used=dec(c.execute("SELECT COALESCE(SUM(kg),0) FROM trades WHERE purchase_id=? AND kind='sell' AND id!=?",(p.purchase_id,trade_id or -1)).fetchone()[0])
            if used+kg>dec(purchase['kg'])+Decimal('.000001'):raise HTTPException(409,'出貨淨重超過批次剩餘重量')
            batch=purchase['batch_key']
        else:
            batch=before['batch_key'] if before else 'HZ-'+uuid.uuid4().hex
            if before and c.execute('SELECT 1 FROM trades WHERE purchase_id=?',(trade_id,)).fetchone():
                fields={'date':str(p.date),'customer_id':customer['id'],'material':p.material,'kg':float(kg),'gross_kg':float(p.gross_kg),'deduction_kg':float(p.deduction_kg),'unit_price':float(p.unit_price),'transport':float(p.transport),'processing':float(p.processing)}
                if any(before[k]!=v for k,v in fields.items()):raise HTTPException(409,'批次已有出貨，不可修改日期、客戶、品項、重量或成本；付款狀態與備註仍可編輯')
        values={'date':str(p.date),'customer_id':customer['id'],'kind':p.kind,'material':p.material,'kg':float(kg),'gross_kg':float(p.gross_kg),'deduction_kg':float(p.deduction_kg),'unit_price':float(p.unit_price),'transport':float(p.transport),'processing':float(p.processing),'payment_status':p.payment_status,'purchase_id':p.purchase_id,'batch_key':batch,'note':p.note,'flow_version':1,'updated_at':now()}
        if before:c.execute('UPDATE trades SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),trade_id))
        else:trade_id=c.execute('INSERT INTO trades('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid
        audit(c,'trade',trade_id,before,values)
        return next(r for r in trade_rows(c) if r['id']==trade_id)

@router.get('/trades')
def trades(kind:Literal['buy','sell']|None=None,customer_key:str|None=None,start:date|None=None,end:date|None=None):
    if start and end and start>end:raise HTTPException(422,'起日不可晚於迄日')
    with connection() as c:
        customer=customer_row(c,customer_key)['id'] if customer_key else None
        return trade_rows(c,kind,customer,start,end)

@router.post('/trades')
def add_trade(p:TradeInput):return save_trade(p)

@router.put('/trades/{trade_id}')
def edit_trade(trade_id:int,p:TradeInput):return save_trade(p,trade_id)

@router.get('/profit')
def profit(start:date|None=None,end:date|None=None,batch_key:str|None=None,sale_price:Decimal|None=Query(None,ge=0,le=1000000),estimated_sale_cost:Decimal=Query(0,ge=0,le=100000000)):
    rows=trades(start=start,end=end)
    if batch_key:rows=[r for r in rows if r['batch_key']==batch_key]
    shipments=[r for r in rows if r['kind']=='sell' and r['realized_profit'] is not None]
    # Inventory is as of end date, independent of report start. Period purchases
    # alone would incorrectly omit unsold lots acquired in an earlier period.
    with connection() as c:all_rows=trade_rows(c,end=end)
    if batch_key:all_rows=[r for r in all_rows if r['batch_key']==batch_key]
    lots=[]
    for p in all_rows:
        if p['kind']!='buy' or p['flow_version']!=1:continue
        sold=sum(dec(r['kg']) for r in all_rows if r['kind']=='sell' and r['purchase_id']==p['id'])
        remaining=dec(p['kg'])-sold;cost=dec(p['total_cost'])-dec(money(dec(p['total_cost'])*sold/dec(p['kg'])))
        lots.append({'batch_key':p['batch_key'],'material':p['material'],'remaining_kg':float(remaining),'unallocated_cost':money(cost)})
    remaining=sum(dec(l['remaining_kg']) for l in lots);unallocated=sum(dec(l['unallocated_cost']) for l in lots)
    return {'realized_profit':money(sum(dec(r['realized_profit']) for r in shipments)),'sales_amount':money(sum(dec(r['amount']) for r in rows if r['kind']=='sell')),'unallocated_cost':money(unallocated),'remaining_kg':float(remaining),'estimated_profit':money(remaining*sale_price-unallocated-estimated_sale_cost) if sale_price is not None else None,'lots':lots,'shipments':shipments,'legacy_unallocated':sum(r['flow_version']==0 for r in rows),'rule':'新台幣／公斤；收購價款＋運費＋加工費按淨重分攤；只有已出貨重量列已實現毛利。未含稅、管理費、利息；情境售價套用全部所選剩餘品項，請優先選單一批次。'}

@router.get('/export')
def export(kind:Literal['buy','sell']|None=None,customer_key:str|None=None,start:date|None=None,end:date|None=None):
    from openpyxl import Workbook
    rows=trades(kind,customer_key,start,end);book=Workbook();sheet=book.active;sheet.title='交易明細'
    keys=['id','date','customer','kind','material','gross_kg','deduction_kg','kg','unit_price','amount','transport','processing','payment_status','batch_key','realized_profit','note']
    sheet.append(['單號','日期','客戶','類型','品項','毛重kg','扣重kg','淨重kg','單價元/kg','金額','運費','加工費','付款狀態','批次','已實現毛利','備註'])
    for r in rows:
        sheet.append([r.get(k) for k in keys])
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value,str):cell.data_type='s' # prevent spreadsheet formula injection
    sheet.freeze_panes='A2';out=io.BytesIO();book.save(out);out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="hongzhou-trades.xlsx"'})
