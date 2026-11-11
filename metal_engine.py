"""Monthly market data, kept separate from daily quotes and trading prices."""
import calendar
import csv
import hashlib
import io
import json
import math
import re
import ssl
import threading
import zipfile
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, ConfigDict, Field, field_validator
import market

router=APIRouter(prefix='/api/metals')
LOCK=threading.Lock()
TAIPEI=timezone(timedelta(hours=8))
WB_URL='https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx'
BLS_URL='https://api.bls.gov/publicAPI/v1/timeseries/data/'
PCC='https://pcic.pcc.gov.tw/pwc-web/api/service/opendata-file/document/'
PROVIDERS={
 'worldbank':dict(name='World Bank Pink Sheet 月度商品價格',url=WB_URL,license='World Bank CC BY 4.0；註明來源與換算，非原始交易所資料授權',terms='https://datacatalog.worldbank.org/public-licenses',frequency='每月；通常次月發布前月資料',history='銅／鋁／鎳自1960；鐵礦砂僅取2009起一致現貨口徑'),
 'bls':dict(name='BLS 美國廢鋼／廢銅價格指數',url=BLS_URL,license='BLS 公有領域資料，註明來源；非廢金屬成交價',terms='https://www.bls.gov/bls/linksite.htm',frequency='月度；可能修訂，初值另標記',history='免金鑰 API 最多10年／次，依實際回傳範圍顯示'),
 **{k:dict(name='工程會 '+name+' 訪價',url=PCC+quote(name+'.csv'),license='政府資料開放授權條款第1版；註明來源',terms='https://data.gov.tw/dataset/'+dataset,frequency='月度調查；目前下載可能僅單月快照',history='只保存實際回傳月份，不能生成缺少的歷史') for k,name,dataset in [('tw_rebar','鋼筋','6820'),('tw_plate','鋼板','6578'),('tw_hbeam','H型鋼','6577')]},
}
WB_COLUMNS={'Copper':('wb_copper','銅'),'Aluminum':('wb_aluminum','鋁'),'Nickel':('wb_nickel','鎳'),'Iron ore, cfr spot':('wb_iron_ore','鐵礦砂 62% Fe CFR')}


class MetalSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    providers:list[str]=Field(default_factory=lambda:list(PROVIDERS),min_length=1,max_length=5)
    update_hours:int=Field(default=24,ge=1,le=168)
    months:int=Field(default=120,ge=12,le=1000)
    threshold:float=Field(default=5,ge=.1,le=100)
    series_a:str=Field(default='wb_copper',max_length=100)
    series_b:str=Field(default='wb_aluminum',max_length=100)
    mode:str='index'
    trend_mode:str='native'
    @field_validator('providers')
    @classmethod
    def valid_providers(cls,v):
        if len(set(v))!=len(v) or any(x not in PROVIDERS for x in v):raise ValueError('來源不合法或重複')
        return v
    @field_validator('mode')
    @classmethod
    def valid_mode(cls,v):
        if v not in ('index','native','twd'):raise ValueError('比較模式不合法')
        return v
    @field_validator('trend_mode')
    @classmethod
    def valid_trend_mode(cls,v):
        if v not in ('native','twd','fx_index'):raise ValueError('趨勢幣別模式不合法')
        return v


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS metal_series(
      id TEXT PRIMARY KEY,name TEXT NOT NULL,provider TEXT NOT NULL,currency TEXT NOT NULL,
      unit TEXT NOT NULL,frequency TEXT NOT NULL,specification TEXT NOT NULL,source_url TEXT NOT NULL,
      license TEXT NOT NULL,notes TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS metal_observations(
      id INTEGER PRIMARY KEY,series_id TEXT NOT NULL REFERENCES metal_series(id),period TEXT NOT NULL,
      value REAL NOT NULL CHECK(value>0),retrieved_at TEXT NOT NULL,source_updated TEXT,notes TEXT NOT NULL,
      UNIQUE(series_id,period,retrieved_at))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_metal_period ON metal_observations(series_id,period,id)')
    c.execute('''CREATE TABLE IF NOT EXISTS metal_updates(
      provider TEXT PRIMARY KEY,checked_at TEXT,success_at TEXT,latest_period TEXT,error TEXT,
      imported INTEGER NOT NULL DEFAULT 0,source_url TEXT,source_updated TEXT)''')
    c.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(4)')


def validate(c):
    for row in c.execute('SELECT * FROM metal_series'):
        if row['provider'] not in PROVIDERS or row['source_url']!=PROVIDERS[row['provider']]['url'] or row['frequency']!='monthly':
            raise ValueError('金屬來源定義不符')
        if row['currency'] not in ('USD','TWD','INDEX') or not row['unit']:
            raise ValueError('金屬單位或幣別不符')
    for row in c.execute('SELECT * FROM metal_observations'):
        if numeric(row['value']) is None:raise ValueError('金屬觀察值為空')
        datetime.strptime(row['period'],'%Y-%m')
        acquired=datetime.fromisoformat(row['retrieved_at'])
        if acquired.tzinfo is None or acquired>datetime.now(timezone.utc):raise ValueError('金屬版本取得時間無效')
        if row['period']>=datetime.now(TAIPEI).strftime('%Y-%m'):raise ValueError('金屬觀察月份尚未完成')


def settings():
    with market.connection() as c:row=c.execute("SELECT value FROM market_settings WHERE name='metals'").fetchone()
    return MetalSettings.model_validate_json(row[0]) if row else MetalSettings()


@router.get('/settings')
def get_settings():return settings().model_dump()


@router.put('/settings')
def save_settings(value:MetalSettings):
    with market.connection() as c:c.execute("INSERT INTO market_settings(name,value) VALUES('metals',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(value.model_dump_json(),))
    return value.model_dump()


def download(provider):
    info=PROVIDERS[provider]
    body=None
    if provider=='bls':
        year=datetime.now(timezone.utc).year
        body=json.dumps({'seriesid':['WPU102301','WPU1012'],'startyear':str(year-9),'endyear':str(year)}).encode()
    headers={'User-Agent':'HongzhouLocalResearch/0.4','Accept':'*/*'}
    if body:headers['Content-Type']='application/json'
    context=ssl.create_default_context();context.load_verify_locations(certifi.where())
    with urlopen(Request(info['url'],data=body,headers=headers),context=context,timeout=20) as response:
        raw=response.read(16*1024*1024+1);modified=response.headers.get('Last-Modified')
    if len(raw)>16*1024*1024:raise ValueError('下載檔案超過16MB')
    return raw,modified


def numeric(v):
    if v is None or str(v).strip() in ('','…','...','-','.'):return None
    x=float(str(v).replace(',','').strip())
    if not math.isfinite(x) or not 0<x<=1e12:raise ValueError('來源有無效數值')
    return x


def series_meta(id,name,provider,currency,unit,spec,notes=''):
    return dict(id=id,name=name,provider=provider,currency=currency,unit=unit,frequency='monthly',specification=spec,
                source_url=PROVIDERS[provider]['url'],license=PROVIDERS[provider]['license'],notes=notes)


def parse_worldbank(raw):
    from openpyxl import load_workbook
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(i.file_size for i in archive.infolist())>80*1024*1024:raise ValueError('XLSX 解壓超過上限')
    book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
    try:
        rows=list(book['Monthly Prices'].values)
        header=next((i for i,r in enumerate(rows[:20]) if 'Copper' in r and 'Aluminum' in r),None)
        if header is None:raise ValueError('World Bank 價格欄位不符')
        descriptions=[' | '.join(str(v) for v in r if v is not None) for r in book['Description'].values]
        updated=next((str(r[0]) for r in rows[:header] if r and str(r[0]).startswith('Updated on ')),None)
        result=[]
        for column,(id,name) in WB_COLUMNS.items():
            index=rows[header].index(column)
            source_unit=str(rows[header+1][index]).strip()
            if id!='wb_iron_ore' and source_unit!='($/mt)':raise ValueError('World Bank 單位改變，拒絕猜測換算')
            if id=='wb_iron_ore' and source_unit not in ('($/dmtu)','($/dmt)','($/mt)'):raise ValueError('鐵礦砂單位不符')
            notes=next((x for x in descriptions if column.split(',')[0].lower()+' (' in x.lower() or (id=='wb_iron_ore' and 'Iron ore, spot' in x)), '')
            if not notes:raise ValueError('缺少商品來源規格說明')
            if id=='wb_iron_ore':notes+=' | 僅取2009年起62% Fe現貨美元／乾公噸；排除早期美分/dmtu合約，不跨口徑拼接。'
            meta=series_meta(id,'World Bank '+name+'月均價','worldbank','USD','USD/dry_t' if id=='wb_iron_ore' else 'USD/t',column,notes+' | 原始表頭單位 '+source_unit)
            values=[]
            for r in rows[header+2:]:
                match=re.fullmatch(r'(\d{4})M(\d{2})',str(r[0]))
                if not match:continue
                period=match[1]+'-'+match[2]
                datetime.strptime(period,'%Y-%m')
                if id=='wb_iron_ore' and period<'2009-01':continue
                value=numeric(r[index])
                if value is not None:values.append((period,value,''))
            if not values:raise ValueError('World Bank 缺少金屬歷史')
            result.append((meta,values))
        return result,updated
    finally:book.close()


def parse_bls(raw):
    data=json.loads(raw)
    if data.get('status')!='REQUEST_SUCCEEDED':raise ValueError('BLS 拒絕請求：'+str(data.get('message'))[:200])
    rows=data['Results']['series']
    definitions={'WPU102301':('bls_copper_scrap','美國廢銅價格指數'),'WPU1012':('bls_steel_scrap','美國廢鋼價格指數')}
    result=[];seen=set()
    for series in rows:
        code=series['seriesID']
        if code not in definitions or code in seen:raise ValueError('BLS 序列不符或重複')
        seen.add(code);values=[]
        for r in series['data']:
            if not re.fullmatch(r'M(0[1-9]|1[0-2])',r['period']):continue
            value=numeric(r['value'])
            if value is not None:values.append((r['year']+'-'+r['period'][1:],value,'；'.join(x.get('text','') for x in r.get('footnotes',[]))))
        if not values:raise ValueError('BLS '+code+' 沒有月觀察')
        id,name=definitions[code]
        meta=series_meta(id,name,'bls','INDEX','Index 1982=100',code+'；未季調','生產者價格指數，非美元報價、非每公斤收購價；近期初值可能修訂。')
        result.append((meta,values))
    if not result:raise ValueError('BLS 沒有序列')
    return result,None


def parse_taiwan(raw,provider):
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:text=raw.decode('cp950')
    reader=csv.DictReader(io.StringIO(text.replace('\r\r\n','\n')))
    if not {'調查時間','調查項目','單位'}.issubset(reader.fieldnames or []):raise ValueError('工程會CSV欄位不符，未猜測日期')
    grouped={}
    for row in reader:
        rawdate=row['調查時間'].strip()
        if not re.fullmatch(r'\d{5}',rawdate):raise ValueError('工程會民國月份格式不符')
        period=f'{int(rawdate[:3])+1911:04d}-{int(rawdate[3:]):02d}'
        datetime.strptime(period,'%Y-%m')
        if row['單位'].strip()!='T':raise ValueError('工程會計價單位不是公噸')
        for region in ('北區','中區','南區','花蓮','台東','不分區金額','不分區價格'):
            value=numeric(row.get(region))
            if value is None:continue
            spec=row['調查項目'].strip()+'／'+region
            id=provider+'_'+hashlib.sha256(spec.encode()).hexdigest()[:12]
            if id not in grouped:grouped[id]=(series_meta(id,PROVIDERS[provider]['name']+' · '+spec,provider,'TWD','TWD/t',spec,'實際調查月份的訪價快照，稅別／運費依官方調查說明；不是豐興牌價。'),[])
            grouped[id][1].append((period,value,''))
    if not grouped:raise ValueError('工程會沒有可用價格列')
    return list(grouped.values()),None


def store(provider,parsed,modified):
    now=datetime.now(timezone.utc).isoformat();count=0;revisions=0
    # Validate the entire provider before changing tables.
    seen={}
    for meta,values in parsed:
        for period,value,note in values:
            numeric(value)
            day=datetime.strptime(period,'%Y-%m').date()
            if period>=datetime.now(TAIPEI).strftime('%Y-%m'):raise ValueError('資料包含未完成月份')
            if (meta['id'],period) in seen and seen[(meta['id'],period)]!=value:raise ValueError('同月同規格來源互相衝突')
            seen[(meta['id'],period)]=value
    with market.connection() as c:
        for meta,values in parsed:
            columns=list(meta)
            c.execute(f"INSERT OR IGNORE INTO metal_series({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",list(meta.values()))
            existing=c.execute('SELECT currency,unit,specification FROM metal_series WHERE id=?',(meta['id'],)).fetchone()
            if tuple(existing)!=(meta['currency'],meta['unit'],meta['specification']):raise ValueError('來源序列定義改變，需人工審核')
            for period,value,note in values:
                old=c.execute('SELECT value,notes FROM metal_observations WHERE series_id=? AND period=? ORDER BY id DESC LIMIT 1',(meta['id'],period)).fetchone()
                if old and math.isclose(old['value'],value,rel_tol=1e-12) and old['notes']==note:continue
                revisions+=bool(old)
                c.execute('INSERT INTO metal_observations(series_id,period,value,retrieved_at,source_updated,notes) VALUES(?,?,?,?,?,?)',(meta['id'],period,value,now,modified,note));count+=1
        latest=max(period for _,values in parsed for period,_,_ in values)
        c.execute('''INSERT INTO metal_updates(provider,checked_at,success_at,latest_period,error,imported,source_url,source_updated)
          VALUES(?,?,?,?,NULL,?,?,?) ON CONFLICT(provider) DO UPDATE SET checked_at=excluded.checked_at,success_at=excluded.success_at,
          latest_period=excluded.latest_period,error=NULL,imported=excluded.imported,source_url=excluded.source_url,source_updated=excluded.source_updated''',
          (provider,now,now,latest,count,PROVIDERS[provider]['url'],modified))
    return count,revisions


def sync(force=True,providers=None):
    if not LOCK.acquire(False):raise HTTPException(409,'金屬資料正在更新')
    try:
        cfg=settings();results=[]
        if providers is not None and any(p not in PROVIDERS for p in providers):raise HTTPException(400,'來源不合法')
        for provider in (cfg.providers if providers is None else [p for p in providers if p in cfg.providers]):
            now=datetime.now(timezone.utc)
            with market.connection() as c:prior=c.execute('SELECT checked_at,error FROM metal_updates WHERE provider=?',(provider,)).fetchone()
            seconds=max(3600 if provider=='bls' else 60,60 if force else cfg.update_hours*3600)
            # One additional request when upgrading the old copper-only BLS feed.
            # Failed attempts still obey cooldown; no repeated retry loop.
            upgrade_bls=provider=='bls' and prior and not prior['error'] and not observations('bls_steel_scrap')
            if prior and not upgrade_bls and (now-datetime.fromisoformat(prior[0])).total_seconds()<seconds:
                results.append({'provider':provider,'ok':not bool(prior['error']),'skipped':True,'message':('上次來源失敗；' if prior['error'] else '')+'尚未到下次檢查時間，沿用本機資料'});continue
            try:
                raw,modified=download(provider)
                parsed,declared=parse_worldbank(raw) if provider=='worldbank' else parse_bls(raw) if provider=='bls' else parse_taiwan(raw,provider)
                if provider=='bls' and {s['id'] for s,_ in parsed}!={'bls_steel_scrap','bls_copper_scrap'}:raise ValueError('BLS未完整回傳廢鋼與廢銅兩種序列，保留舊資料')
                count,revisions=store(provider,parsed,declared or modified)
                results.append({'provider':provider,'ok':True,'imported':count,'revisions':revisions,'message':f'新增 {count} 筆版本，其中修訂 {revisions} 筆；原版本保留'})
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning('Metal provider %s failed: %s',provider,exc)
                error=type(exc).__name__+': '+str(exc)[:250]
                with market.connection() as c:c.execute('INSERT INTO metal_updates(provider,checked_at,error) VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET checked_at=excluded.checked_at,error=excluded.error',(provider,now.isoformat(),error))
                results.append({'provider':provider,'ok':False,'message':error})
        return {'results':results,'ok':all(r['ok'] for r in results)}
    finally:LOCK.release()


@router.post('/sync')
def update():return sync(True)


@router.post('/import-file')
async def import_file(provider:str=Form(...),file:UploadFile=File(...)):
    if provider not in PROVIDERS:raise HTTPException(400,'請選擇官方來源')
    raw=await file.read(16*1024*1024+1)
    if len(raw)>16*1024*1024:raise HTTPException(400,'檔案上限16MB')
    if not LOCK.acquire(False):raise HTTPException(409,'資料正在更新，稍後再匯入')
    try:
        suffix=(file.filename or '').lower()
        official_wb=False
        if provider=='worldbank' and suffix.endswith('.xlsx'):
            from openpyxl import load_workbook
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if sum(x.file_size for x in z.infolist())>80*1024*1024:raise ValueError('Excel解壓超過上限')
            book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
            try:official_wb='Monthly Prices' in book.sheetnames
            finally:book.close()
        if official_wb:parsed,modified=parse_worldbank(raw)
        elif provider.startswith('tw_') and suffix.endswith('.csv') and not raw.lstrip(b'\xef\xbb\xbf').startswith(b'series_id,'):parsed,modified=parse_taiwan(raw,provider)
        elif provider=='bls' and suffix.endswith('.json'):parsed,modified=parse_bls(raw)
        else:
            # Normalized CSV/XLSX supports existing known definitions only.
            if suffix.endswith('.csv'):
                records=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            elif suffix.endswith('.xlsx'):
                from openpyxl import load_workbook
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    if sum(x.file_size for x in z.infolist())>80*1024*1024:raise ValueError('Excel解壓超過上限')
                book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
                try:
                    values=book.active.iter_rows(values_only=True);headers=next(values)
                    records=[]
                    for row in values:
                        if len(records)>=5000:raise ValueError('最多5000筆')
                        records.append(dict(zip(headers,row)))
                finally:book.close()
            else:raise ValueError('支援官方Excel／CSV／BLS JSON，或標準化CSV／XLSX')
            if not records or len(records)>5000:raise ValueError('需1至5000筆資料')
            catalog={s['id']:s for s in all_series() if s['provider']==provider};group={}
            for row in records:
                if not {'series_id','period','value','source_url'}.issubset(row):raise ValueError('標準欄位：series_id,period,value,source_url')
                s=catalog.get(row['series_id'])
                if not s or row['source_url']!=s['source_url']:raise ValueError('請先更新官方資料建立規格，且來源網址須與選定來源完全相同')
                value=numeric(row['value'])
                if value is None:raise ValueError('價格不得空白')
                group.setdefault(s['id'],(s,[]))[1].append((str(row['period']),value,'使用者檔案匯入；請核對原始官方來源'))
            parsed=list(group.values());modified=None
        # Record that the transport was a user file, rather than a successful HTTP fetch.
        parsed=[(s,[(p,v,(n+'；使用者檔案匯入').strip('；')) for p,v,n in rows]) for s,rows in parsed]
        count,revisions=store(provider,parsed,modified)
        with market.connection() as c:c.execute('UPDATE metal_updates SET source_updated=? WHERE provider=?',('使用者檔案匯入，非自動下載',provider))
        return {'ok':True,'message':f'檔案匯入成功：{count}筆版本，含{revisions}筆修訂；舊資料保留。'}
    except Exception as exc:raise HTTPException(400,'檔案未匯入：'+str(exc)[:200]) from exc
    finally:LOCK.release()


@router.get('/status')
def status():
    with market.connection() as c:stored={r['provider']:dict(r) for r in c.execute('SELECT * FROM metal_updates')}
    cfg=settings()
    return [{'provider':p,**info,**stored.get(p,{}),'enabled':p in cfg.providers,'api_key':'不需要','delay':'月度／延遲，非即時'} for p,info in PROVIDERS.items()]


def all_series():
    with market.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM metal_series ORDER BY provider,id')]


def observations(id):
    with market.connection() as c:rows=c.execute('SELECT * FROM metal_observations WHERE series_id=? ORDER BY period,id',(id,)).fetchall()
    latest={r['period']:dict(r) for r in rows}
    return list(latest.values())


def representative_settings():
    with market.connection() as c:row=c.execute("SELECT value FROM market_settings WHERE name='steel_representatives'").fetchone()
    saved=json.loads(row[0]) if row else {}
    result={}
    for provider in ('tw_rebar','tw_hbeam','tw_plate'):
        candidates=[s for s in all_series() if s['provider']==provider and observations(s['id'])]
        if not candidates:result[provider]=None;continue
        # Prefer the fixed rebar specification. Never mix regions or average prices.
        candidates.sort(key=lambda s:(not (provider=='tw_rebar' and 'SD280，' in s['specification'] and 'D16mm' in s['specification'] and '北區' in s['specification']),-len(observations(s['id'])),s['specification']))
        ids={s['id'] for s in candidates}
        result[provider]=saved.get(provider) if saved.get(provider) in ids else candidates[0]['id']
    if any(result.values()) and result!=saved:
        with market.connection() as c:c.execute("INSERT INTO market_settings(name,value) VALUES('steel_representatives',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(result),))
    return result


class Representatives(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tw_rebar:str|None=None
    tw_hbeam:str|None=None
    tw_plate:str|None=None


@router.get('/representatives')
def get_representatives():return representative_settings()


@router.put('/representatives')
def save_representatives(value:Representatives):
    ids={s['id']:s for s in all_series()}
    for provider,id in value.model_dump().items():
        if id is not None and (id not in ids or ids[id]['provider']!=provider or not observations(id)):
            raise HTTPException(400,'代表規格不存在、沒有資料或商品類別不符')
    with market.connection() as c:c.execute("INSERT INTO market_settings(name,value) VALUES('steel_representatives',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(value.model_dump_json(),))
    return representative_settings()


def display_name(s):
    names={'wb_copper':'國際銅價（月均價）','wb_aluminum':'國際鋁價（月均價）','wb_nickel':'國際鎳價（月均價）','wb_iron_ore':'國際鐵礦砂（月均價）','bls_steel_scrap':'美國廢鋼價格指數','bls_copper_scrap':'美國廢銅價格指數'}
    if s['id'] in names:return names[s['id']]
    if s['provider'].startswith('tw_'):
        kind={'tw_rebar':'鋼筋','tw_hbeam':'H型鋼','tw_plate':'鋼板'}[s['provider']]
        spec=s['specification'].replace('產品，','').replace(kind+'，','').replace('，熱軋','').replace('，', ' ')
        region=s['specification'].split('／')[-1].replace('不分區價格','不分區').replace('不分區金額','不分區')
        if s['provider']=='tw_rebar':
            grade=re.search(r'SD\d+\w*',spec);size=re.search(r'D\d+mm',spec)
            if grade and size:spec=grade[0]+' '+size[0]+'／'+region
        elif s['provider']=='tw_hbeam':
            size=re.search(r'H\d+×B\d+',spec)
            if size:spec=size[0]+'／'+region
        elif s['provider']=='tw_plate':
            parts=s['specification'].split('，')
            if len(parts)>=2:spec=' '.join(parts[-2:]).split('／')[0]+'／'+region
        return '台灣'+kind+'（'+spec+'）'
    return s['name']


@router.get('/series')
def get_series():
    result=[];reps=representative_settings()
    for s in all_series():
        rows=observations(s['id'])
        if rows:result.append({**s,'display_name':display_name(s),'category':'steel' if s['provider'].startswith('tw_') or s['id']=='bls_steel_scrap' else 'metals', 'representative':not s['provider'].startswith('tw_') and s['id']!='bls_copper_scrap' or s['id'] in reps.values(),'count':len(rows),'first_period':rows[0]['period'],'latest':rows[-1]})
    return result
