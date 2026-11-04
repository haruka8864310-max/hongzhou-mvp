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
from fastapi import APIRouter, HTTPException
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
 'bls':dict(name='BLS 美國廢銅價格指數',url=BLS_URL,license='BLS 公有領域資料，註明來源；非廢銅成交價',terms='https://www.bls.gov/bls/linksite.htm',frequency='月度；可能修訂，初值另標記',history='免金鑰 API 最多10年／次，依實際回傳範圍顯示'),
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
        body=json.dumps({'seriesid':['WPU102301'],'startyear':str(year-9),'endyear':str(year)}).encode()
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
    if len(rows)!=1 or rows[0]['seriesID']!='WPU102301':raise ValueError('BLS 序列不符')
    values=[]
    for r in rows[0]['data']:
        if not re.fullmatch(r'M(0[1-9]|1[0-2])',r['period']):continue
        value=numeric(r['value'])
        if value is not None:values.append((r['year']+'-'+r['period'][1:],value,'；'.join(x.get('text','') for x in r.get('footnotes',[]))))
    if not values:raise ValueError('BLS 沒有月觀察')
    meta=series_meta('bls_copper_scrap','BLS 美國廢銅價格指數','bls','INDEX','Index 1982=100','WPU102301；未季調',
                     '生產者價格指數，非美元報價、非每公斤收購價；近期初值可能修訂。')
    return [(meta,values)],None


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


def sync(force=True):
    if not LOCK.acquire(False):raise HTTPException(409,'金屬資料正在更新')
    try:
        cfg=settings();results=[]
        for provider in cfg.providers:
            now=datetime.now(timezone.utc)
            with market.connection() as c:prior=c.execute('SELECT checked_at,error FROM metal_updates WHERE provider=?',(provider,)).fetchone()
            seconds=max(3600 if provider=='bls' else 60,60 if force else cfg.update_hours*3600)
            if prior and (now-datetime.fromisoformat(prior[0])).total_seconds()<seconds:
                results.append({'provider':provider,'ok':not bool(prior['error']),'skipped':True,'message':('上次來源失敗；' if prior['error'] else '')+'尚未到下次檢查時間，沿用本機資料'});continue
            try:
                raw,modified=download(provider)
                parsed,declared=parse_worldbank(raw) if provider=='worldbank' else parse_bls(raw) if provider=='bls' else parse_taiwan(raw,provider)
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


@router.get('/series')
def get_series():
    result=[]
    for s in all_series():
        rows=observations(s['id'])
        if rows:result.append({**s,'count':len(rows),'first_period':rows[0]['period'],'latest':rows[-1]})
    return result
