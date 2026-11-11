"""Additive, revision-preserving market ledger and conservative point-in-time reads."""
import csv,hashlib,io,json,math,threading,re
from datetime import date,datetime,timezone,timedelta
from urllib.parse import urlsplit
from fastapi import APIRouter,HTTPException,UploadFile,File
from pydantic import BaseModel,Field,ConfigDict,model_validator
import market,market_sources,metal_engine as engine,metal_analysis as analysis
from global_catalog import CATALOG,BY_ID,COUNTRIES

router=APIRouter(prefix='/api/global')
LOCK=threading.Lock()


def now():return datetime.now(timezone.utc).isoformat()


def utc(stamp):
    value=datetime.fromisoformat(stamp.replace('Z','+00:00')) if isinstance(stamp,str) else stamp
    if value.tzinfo is None:raise ValueError('時間須包含時區')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


class Observation(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True,allow_inf_nan=False)
    market:str
    grade:str=Field(min_length=1,max_length=250)
    delivery_term:str=Field(min_length=1,max_length=250)
    currency:str=Field(min_length=2,max_length=10)
    unit:str
    price:float|None=Field(default=None,gt=0,le=1e12)
    adjustment:float|None=Field(default=None,ge=-1e12,le=1e12)
    quote_date:str
    published_at:datetime|None=None
    frequency:str
    source:str=Field(min_length=1,max_length=150)
    source_url:str=Field(min_length=1,max_length=1000)
    license_note:str=Field(min_length=1,max_length=500)
    @model_validator(mode='after')
    def valid(self):
        if self.market not in BY_ID:raise ValueError('市場代碼無效')
        if self.frequency not in ('daily','weekly','monthly','annual','observed'):raise ValueError('頻率無效')
        pattern='%Y-%m' if self.frequency=='monthly' else '%Y' if self.frequency=='annual' else '%Y-%m-%d'
        day=datetime.strptime(self.quote_date,pattern).date()
        if day.strftime(pattern)!=self.quote_date or day>datetime.now(engine.TAIPEI).date():raise ValueError('日期格式或未來日期無效')
        if self.frequency=='monthly' and self.quote_date>=datetime.now(engine.TAIPEI).strftime('%Y-%m'):raise ValueError('月資料須為已完成月份')
        if self.price is None and self.adjustment is None:raise ValueError('須有價格或調價；平盤只填調價0')
        if self.currency not in ('TWD','USD','JPY','CNY','KRW','INR','VND','INDEX','NA'):raise ValueError('幣別不支援')
        if self.unit not in ('t','kg','lb','short_ton','dry_t','index','million_t','TWD per USD','TWD per JPY','TWD per CNY','JPY per USD','USD per t'):raise ValueError('單位不支援')
        if (self.currency=='INDEX')!=(self.unit=='index'):raise ValueError('價格指數與貨幣價格不能混用')
        if self.market in ('us_index','in_demand') and self.currency!='INDEX':raise ValueError('此項目是指數，不能填成交價格')
        if self.market=='steel_output' and (self.unit!='million_t' or self.currency!='NA'):raise ValueError('產量須為NA幣別及million_t，非價格')
        if self.market=='jp_fob' and 'FOB' not in self.delivery_term.upper():raise ValueError('日本出口FOB項目須明列FOB交貨條件')
        if self.market in ('us_cfr_tw','tr_hms','vn_hms','vn_h2','in_scrap') and 'CFR' not in self.delivery_term.upper():raise ValueError('此進口市場須明列CFR交貨條件')
        if self.published_at:
            if self.published_at.tzinfo is None or self.published_at>datetime.now(timezone.utc):raise ValueError('發布時間須含時區且不可在未來')
            if self.published_at.astimezone(engine.TAIPEI).date()<day:raise ValueError('發布時間不能早於報價期')
        url=urlsplit(self.source_url)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:raise ValueError('來源網址須為不含帳密的http/https')
        return self


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS global_quotes(
      id INTEGER PRIMARY KEY,series_id TEXT NOT NULL,origin TEXT NOT NULL UNIQUE,
      market TEXT NOT NULL,country TEXT NOT NULL,instrument TEXT NOT NULL,grade TEXT NOT NULL,
      delivery_term TEXT NOT NULL,currency TEXT NOT NULL,unit TEXT NOT NULL,price REAL,
      adjustment REAL,quote_date TEXT NOT NULL,published_at TEXT,first_seen_at TEXT NOT NULL,
      retrieved_at TEXT NOT NULL,source TEXT NOT NULL,source_url TEXT NOT NULL,data_status TEXT NOT NULL,
      frequency TEXT NOT NULL,license_note TEXT NOT NULL,
      CHECK(price IS NULL OR (price>0 AND price<=1e12)),CHECK(price IS NOT NULL OR adjustment IS NOT NULL))''')
    c.execute('CREATE INDEX IF NOT EXISTS global_series_vintage ON global_quotes(series_id,quote_date,retrieved_at,id)')
    c.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(5)')


def insert(c,value,retrieved_at,origin,status='manual',first_seen=None):
    retrieved_at=utc(retrieved_at)
    value=value if isinstance(value,Observation) else Observation.model_validate(value)
    r=value.model_dump(mode='json');item=BY_ID[value.market]
    identity=[r[k] for k in ('market','grade','delivery_term','currency','unit','frequency','source','source_url')]
    series_id=hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
    previous=c.execute('SELECT MIN(first_seen_at) FROM global_quotes WHERE series_id=? AND quote_date=?',(series_id,r['quote_date'])).fetchone()[0]
    r.update(series_id=series_id,origin=origin,country=item['country'],instrument=item['name'],retrieved_at=retrieved_at,
             first_seen_at=min(previous or retrieved_at,utc(first_seen or retrieved_at),retrieved_at),data_status=status)
    if r['published_at']:r['published_at']=utc(value.published_at)
    columns=list(r)
    return c.execute(f"INSERT OR IGNORE INTO global_quotes({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",list(r.values())).rowcount


def record_fx(c,q,provider,stamp):
    """Keep feed revisions even though the old daily table intentionally keeps its first value."""
    previous=c.execute('SELECT price FROM global_quotes WHERE market=? AND grade=? AND delivery_term=? AND source_url=? AND quote_date=? ORDER BY retrieved_at DESC,id DESC LIMIT 1',
        (q.instrument,q.specification,q.contract,q.source_url,q.date.isoformat())).fetchone()
    if previous and math.isclose(previous[0],q.price,rel_tol=1e-10):return
    insert(c,dict(market=q.instrument,grade=q.specification,delivery_term=q.contract,currency='TWD',unit=BY_ID[q.instrument]['unit'],price=q.price,quote_date=q.date.isoformat(),frequency='daily',published_at=None,source=q.source,source_url=q.source_url,license_note=q.license_note),stamp,'fx-feed:'+hashlib.sha256((q.model_dump_json()+stamp).encode()).hexdigest(),'automatic')


def capture_cross(c):
    grouped={};count=0
    for r in c.execute("SELECT * FROM global_quotes WHERE market IN ('fx_usd','fx_jpy') ORDER BY retrieved_at,id").fetchall():
        grouped.setdefault((r['quote_date'],r['source_url'],r['source'],r['delivery_term'],r['grade']),{})[r['market']]=dict(r)
    for pair in grouped.values():
        if set(pair)!={'fx_usd','fx_jpy'}:continue
        a,b=pair['fx_usd'],pair['fx_jpy']
        count+=insert(c,dict(market='jpy_per_usd',grade='同日USD/TWD÷JPY/TWD：'+a['grade'],delivery_term=a['delivery_term'],currency='JPY',unit='JPY per USD',price=a['price']/b['price'],quote_date=a['quote_date'],published_at=None,frequency='daily',source=a['source'],source_url=a['source_url'],license_note=a['license_note']),max(a['retrieved_at'],b['retrieved_at']),f"cross-global:{a['id']}:{b['id']}",'derived')
    return count


def capture_existing():
    """Preserve known original acquisition times; never backdate a downloaded snapshot."""
    count=0
    with market.connection() as c:
        for row in c.execute('SELECT o.*,s.provider,s.name,s.currency,s.unit,s.specification,s.source_url,s.license FROM metal_observations o JOIN metal_series s ON s.id=o.series_id ORDER BY o.id').fetchall():
            if row['provider']=='worldbank':target='global_'+row['series_id'][3:]
            elif row['provider']=='bls':
                if row['series_id']!='bls_steel_scrap':continue
                target='us_index'
            else:target='pcc_'+row['provider'][3:]
            unit='index' if row['currency']=='INDEX' else row['unit'].split('/')[-1]
            count+=insert(c,dict(market=target,grade=row['specification'],delivery_term=BY_ID[target]['delivery_term'],currency=row['currency'],unit=unit,price=row['value'],quote_date=row['period'],published_at=None,frequency='monthly',source=engine.PROVIDERS[row['provider']]['name'],source_url=row['source_url'],license_note=row['license']),row['retrieved_at'],'metal:'+str(row['id']),'imported' if '使用者檔案' in row['notes'] else 'snapshot')
        quotes=c.execute('SELECT * FROM market_quotes ORDER BY id').fetchall();fx={}
        mapping={'fh_scrap':'tw_scrap','fh_rebar':'tw_rebar','jp_h2':'jp_h2','us_scrap':'us_legacy','fx_usd':'fx_usd','fx_jpy':'fx_jpy','fx_cny':'fx_cny'}
        for row in quotes:
            if row['instrument'] not in mapping:continue
            target=mapping[row['instrument']];isfx=row['instrument'].startswith('fx_')
            count+=insert(c,dict(market=target,grade=row['specification'],delivery_term=row['contract'],currency=row['currency'],unit=BY_ID[target]['unit'] if isfx else row['unit'],price=row['price'],quote_date=row['date'],published_at=row['published_at'] if row['publication_known'] else None,frequency='daily' if isfx else 'weekly',source=row['source'],source_url=row['source_url'],license_note=row['license_note']),row['updated_at'],'quote:'+str(row['id']),'snapshot')
            if row['instrument'] in ('fx_usd','fx_jpy'):fx.setdefault((row['date'],row['method'],row['source_url']),{})[row['instrument']]=row
        count+=capture_cross(c)
        saved=c.execute("SELECT value FROM market_settings WHERE name='steel_bulletins'").fetchone()
        for row in json.loads(saved[0]) if saved else []:
            import market_detail
            target='tw_rebar_monthly' if row['cadence']=='monthly' else {'scrap':'tw_scrap','rebar':'tw_rebar','sections':'tw_sections'}[row['product']]
            source=market_detail.SOURCES[row['source']]
            count+=insert(c,dict(market=target,grade=row['specification'],delivery_term='依來源公告，稅運費未詳',currency='TWD',unit='t',price=row['price'],adjustment=row['change'],quote_date=row['period'],published_at=None,frequency=row['cadence'],source=source['name'],source_url=source['url'],license_note=row['permission_note']),row['updated_at'],'bulletin:'+row['key']+':'+row['period'],'manual')
    return count


@router.post('/quotes')
def add(value:Observation):
    stamp=now()
    with market.connection() as c:insert(c,value,stamp,'manual:'+hashlib.sha256((value.model_dump_json()+stamp).encode()).hexdigest())
    return {'ok':True,'message':'已追加新版本；相同日期的舊版本保留，取得時間由系統記錄。'}


@router.get('/template')
def template():
    from fastapi.responses import Response
    return Response('\ufeff'+','.join(Observation.model_fields)+'\r\n',media_type='text/csv',headers={'Content-Disposition':'attachment; filename=global-market.csv'})


@router.post('/import')
async def import_csv(file:UploadFile=File(...)):
    raw=await file.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise HTTPException(400,'CSV上限2MB')
    try:
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')));values=[]
        if set(reader.fieldnames or [])!=set(Observation.model_fields):raise ValueError('請使用下載的CSV欄位，不可缺少規格、條件與使用依據')
        for row in reader:
            if len(values)>=5000:raise ValueError('最多5000筆')
            for k in ('price','adjustment','published_at'):
                if row[k]=='':row[k]=None
            values.append(Observation.model_validate(row))
        if not values:raise ValueError('CSV沒有資料')
        identities=[v.model_dump_json() for v in values]
        if len(set(identities))!=len(identities):raise ValueError('檔內有重複列，整批取消')
        stamp=now()
        with market.connection() as c:
            for i,v in enumerate(values):insert(c,v,stamp,'csv:'+hashlib.sha256((v.model_dump_json()+stamp+str(i)).encode()).hexdigest(),'imported')
        return {'ok':True,'message':f'已驗證並追加{len(values)}筆版本，舊版本未覆蓋。'}
    except Exception as exc:raise HTTPException(400,'未匯入：'+str(exc)[:250]) from exc


def available(as_of=None):
    stamp=datetime.now(timezone.utc) if as_of is None else datetime.fromisoformat(as_of.replace('Z','+00:00'))
    if stamp.tzinfo is None:raise HTTPException(400,'當時可得查詢必須包含時區')
    stamp=utc(stamp)
    with market.connection() as c:rows=c.execute('SELECT * FROM global_quotes WHERE retrieved_at<=? AND first_seen_at<=? AND (published_at IS NULL OR published_at<=?) ORDER BY retrieved_at,id',(stamp,stamp,stamp)).fetchall()
    # All vintage timestamps are normalized UTC before writing.
    result={}
    for row in rows:
        value=dict(row)
        if value['origin'].startswith('tokyo|'):
            value['document_url']=value['origin'].split('|')[1]
            issued=re.search(r'公告日(\d{4}-\d{2}-\d{2})',value['license_note'])
            value['published_date']=issued[1] if issued else None
        if value['origin'].startswith('nbs|'):
            value['document_url']=value['origin'].split('|')[1]
        result[(row['series_id'],row['quote_date'])]=value
    return sorted(result.values(),key=lambda r:(r['quote_date'],r['id']))


def stale(row):
    if row['frequency']=='monthly':return row['quote_date']<analysis.shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2)
    if row['frequency']=='annual':return int(row['quote_date'])<datetime.now(engine.TAIPEI).year-1
    return (datetime.now(engine.TAIPEI).date()-date.fromisoformat(row['quote_date'])).days>(4 if row['frequency']=='daily' else 10)


def statuses():
    import china_sources
    monthly={r['provider']:r for r in engine.status()};fx=market_sources.source_status()['sources']
    import japan_sources
    tokyo=japan_sources.state()
    result={}
    for item in CATALOG:
        state=china_sources.state() if item['provider']=='nbs' else tokyo if item['provider']=='tokyo' else monthly.get(item['provider'],{}) if item['provider']!='fx' else next((r for r in fx if r['instrument']=='fx_usd'),{})
        imported='使用者檔案' in str(state.get('source_updated',''))
        reason=item['reason']
        if item['id'].startswith('pcc_') and state.get('latest_period'):reason='來源目前下載最新月份為'+state['latest_period']+'。'+reason
        result[item['id']]={'automatic':bool(item['provider']),'last_success_at':None if imported else state.get('success_at'),'last_import_at':state.get('success_at') if imported else None,'checked_at':state.get('checked_at'),'error':state.get('error'),'frequency':item['frequency'],'reason':reason,'source_url':state.get('url') or item['url'],'license':japan_sources.FACT_USE if item['provider']=='tokyo' else state.get('license') or item['license']}
    return result


@router.get('/overview')
def overview(country:str|None=None,as_of:str|None=None):
    if country and country not in COUNTRIES:raise HTTPException(400,'國家無效')
    try:rows=available(as_of)
    except ValueError as exc:raise HTTPException(400,'當時可得時間格式無效') from exc
    states=statuses();items=[];representatives=engine.representative_settings()
    representative_grades={s['id']:s['specification'] for s in engine.all_series()}
    for item in CATALOG:
        if country and country!=item['country']:continue
        groups={}
        for r in rows:
            if r['market']==item['id']:groups.setdefault(r['series_id'],[]).append(r)
        series=[]
        for key,values in groups.items():
            last=values[-1];priced=[r for r in values if r['price'] is not None]
            if item['id'].startswith('pcc_'):
                chosen=representative_grades.get(representatives.get('tw_'+item['id'][4:]))
                if chosen and last['grade']!=chosen:continue
            change=None
            if last['price'] is not None and len(priced)>1:
                base=priced[-2];change={'absolute':last['price']-base['price'],'percent':(last['price']/base['price']-1)*100,'base_date':base['quote_date']}
            series.append({'id':key,'latest':last,'count':len(values),'change':change,'stale':stale(last)})
        state=states[item['id']]
        items.append({**item,'state':state,'series':series,'status':'來源失敗（歷史保留）' if state['error'] else '尚無同規格資料' if not series else '資料過期' if all(s['stale'] for s in series) else '月度延遲／參考指數' if item['frequency']=='monthly' else '已有參考資料，非即時'})
    return {'countries':COUNTRIES,'items':items,'as_of':as_of,'warning':'發布未知者只從首次實際取得後才可用；今天下載的歷史不會視為過去已知。不同國家／品級／交貨條件的價差不是套利。'}


def converted(row,rows):
    if row['currency']=='TWD':return row['price']
    if row['currency'] not in ('USD','JPY','CNY') or row['price'] is None:return None
    groups={};target='fx_'+row['currency'].lower()
    for r in rows:
        if r['market']==target:groups.setdefault(r['series_id'],[]).append(r)
    preferred=market_sources.provider_info(market_sources.get_settings().provider)['url']
    chosen=[v for v in groups.values() if v[-1]['source_url']==preferred]
    candidates=chosen if len(chosen)==1 else list(groups.values())
    if len(candidates)!=1:return None
    if row['frequency']=='monthly':
        rates=[r['price'] for r in candidates[0] if r['quote_date'][:7]==row['quote_date'] and r['price'] is not None]
        return row['price']*sum(rates)/len(rates) if len(rates)>=10 else None
    if row['frequency']=='annual':return None
    previous=[r for r in candidates[0] if r['quote_date']<=row['quote_date'] and r['price'] is not None]
    if not previous:return None
    rate=max(previous,key=lambda r:r['quote_date'])
    return row['price']*rate['price'] if (date.fromisoformat(row['quote_date'])-date.fromisoformat(rate['quote_date'])).days<=7 else None


@router.get('/history')
def history(series:str,months:int=12,mode:str='native',as_of:str|None=None):
    if months not in (0,1,3,6,12,36) or mode not in ('native','twd','index'):raise HTTPException(400,'期間或計價模式無效')
    try:all_rows=available(as_of)
    except ValueError as exc:raise HTTPException(400,'時間格式無效') from exc
    rows=[r for r in all_rows if r['series_id']==series]
    if not rows:raise HTTPException(404,'此查詢時間沒有已取得的序列，不以後來的歷史回填')
    latest=rows[-1];points=[];missing=0
    if latest['frequency']=='annual':
        selected=[r for r in rows if not months or int(r['quote_date'])>=int(latest['quote_date'])-max(1,months//12)+1]
    else:selected=analysis.window([dict(r,period=r['quote_date']) for r in rows],months) if latest['frequency']=='monthly' and months else rows
    if latest['frequency'] not in ('monthly','annual') and months:
        import market_detail
        selected=market_detail.ranged([dict(r,period=r['quote_date']) for r in rows],months,latest['frequency'])
    for r in selected:
        value=converted(r,all_rows) if mode=='twd' else r['price']
        if value is None:missing+=1;continue
        points.append({'period':r['quote_date'],'value':value,'original_price':r['price'],'retrieved_at':r['retrieved_at'],'published_at':r['published_at'],'published_date':r.get('published_date'),'source_url':r.get('document_url') or r['source_url']})
    unit=latest['currency']+'/'+latest['unit'] if mode=='native' else 'TWD/'+latest['unit']
    if mode=='index':
        base=points[0]['value'] if points else 1
        points=[{**p,'value':p['value']/base*100} for p in points];unit='本期首筆=100'
    change={'absolute':points[-1]['value']-points[0]['value'],'percent':(points[-1]['value']/points[0]['value']-1)*100} if len(points)>1 else None
    reason='只連接原始頻率觀察點，不補值。台幣換算保留原物理單位；原幣／標準化變化不代表套利。'
    if len(points)<2:
        reason='至少兩筆實際價格才能顯示折線；調價0不是價格0，指數與產量不是廢鋼成交價。'
        if latest['price'] is None:reason='只有調價資訊，缺少基準絕對價格；平盤不能代替價格，未繪製假圖。'
        elif latest['market'].startswith('pcc_') and len(rows)==1:reason='工程會目前僅'+latest['quote_date']+'單月快照，缺少第二個同規格歷史觀察；未繪製假折線。'
        elif mode=='twd' and not points:reason='缺少同日／同月足量且合法可用的匯率，或此為指數不能換算成台幣價格。'
    return {'latest':latest,'points':points,'unit':unit,'change':change,'missing':missing,'stale':stale(latest),'reason':reason}


@router.get('/compare')
def compare(a:str,b:str,months:int=12,mode:str='index',as_of:str|None=None):
    if a==b:raise HTTPException(400,'請選兩個不同序列')
    if mode not in ('index','native','twd'):raise HTTPException(400,'比較方式無效')
    left=history(a,months,'twd' if mode=='twd' else 'native',as_of);right=history(b,months,'twd' if mode=='twd' else 'native',as_of)
    x,y=left['latest'],right['latest']
    if x['frequency']!=y['frequency']:raise HTTPException(400,'頻率不同；請選同頻率，不把月價複製成日價')
    if mode!='index' and (x['unit']!=y['unit'] or mode=='native' and x['currency']!=y['currency']):raise HTTPException(400,'原始幣別或單位不同，請改標準化比較')
    r={p['period']:p['value'] for p in right['points']};points=[{'period':p['period'],'a':p['value'],'b':r[p['period']]} for p in left['points'] if p['period'] in r]
    if mode=='index' and points:
        first=points[0].copy();points=[{**p,'a':p['a']/first['a']*100,'b':p['b']/first['b']*100} for p in points]
    return {'points':points,'a':x,'b':y,'unit':'共同首期100' if mode=='index' else left['unit'],'reason':'不同國家、品質與FOB／CFR等條件仍不同，只比較走勢，不能直接視為套利價差。'+(' 不足兩筆共同日期，不繪線。' if len(points)<2 else '')}


@router.post('/refresh')
def refresh():
    if not LOCK.acquire(False):raise HTTPException(409,'來源正在更新')
    results=[]
    try:
        import japan_sources
        for label,fn in [('東京製鐵官方數值',lambda:japan_sources.sync(True)),('月度來源',lambda:engine.sync(True)),('日參考匯率',lambda:market_sources.synchronize(True))]:
            try:results.append({'name':label,**fn()})
            except Exception as exc:results.append({'name':label,'ok':False,'message':str(exc)[:250]})
        added=capture_existing()
        return {'ok':all(r.get('ok') for r in results),'results':results,'versions_added':added,'message':'來源已檢查；新增版本不等於報價日已更新，請看各市場報價日期。'}
    finally:LOCK.release()


@router.post('/capture')
def capture():return {'ok':True,'versions_added':capture_existing(),'message':'只整理本機既有資料，未連外下載。'}


class Preferences(BaseModel):
    model_config=ConfigDict(extra='forbid')
    country:str='TW'
    months:int=12
    mode:str='native'
    @model_validator(mode='after')
    def valid(self):
        if self.country not in COUNTRIES or self.months not in (0,1,3,6,12,36) or self.mode not in ('native','twd','index'):raise ValueError('國家、期間或計價不符')
        return self


@router.get('/settings')
def preferences():
    with market.connection() as c:r=c.execute("SELECT value FROM market_settings WHERE name='global_ui'").fetchone()
    return Preferences.model_validate_json(r[0]).model_dump() if r else Preferences().model_dump()


@router.put('/settings')
def save_preferences(value:Preferences):
    with market.connection() as c:c.execute("INSERT INTO market_settings(name,value) VALUES('global_ui',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(value.model_dump_json(),))
    return value.model_dump()


def validate(c):
    columns={r[1] for r in c.execute('PRAGMA table_info(global_quotes)')}
    if not (set(Observation.model_fields)|{'country','instrument','first_seen_at','retrieved_at','source','source_url','data_status','series_id','origin','id'})<=columns:raise ValueError('統一行情欄位不完整')
    for row in c.execute('SELECT * FROM global_quotes'):
        Observation.model_validate({k:row[k] for k in Observation.model_fields})
        if row['country']!=BY_ID[row['market']]['country']:raise ValueError('統一行情國家不符')
        first=datetime.fromisoformat(row['first_seen_at']);retrieved=datetime.fromisoformat(row['retrieved_at'])
        if first.tzinfo is None or retrieved.tzinfo is None or first>retrieved or retrieved>datetime.now(timezone.utc):raise ValueError('統一行情版本時間無效')
        if row['first_seen_at']!=utc(first) or row['retrieved_at']!=utc(retrieved):raise ValueError('版本時間須標準化UTC，避免日期排序錯誤')
        if row['data_status'] not in ('snapshot','manual','imported','automatic','derived'):raise ValueError('統一行情狀態無效')
    r=c.execute("SELECT value FROM market_settings WHERE name='global_ui'").fetchone()
    if r:Preferences.model_validate_json(r[0])
