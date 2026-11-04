"""Local market intelligence; no external quote requests or seeded market data."""
import csv
import io
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

router = APIRouter(prefix='/api/market')

# Unit is explicit: t = metric tonne, short_ton = 907.18474 kg.
CATALOG = [
    dict(id='fh_scrap', name='豐興廢鋼收購價', group='steel', currency='TWD', unit='t', cadence=10, url='https://www.fenghsin.com.tw/'),
    dict(id='fh_rebar', name='豐興鋼筋', group='steel', currency='TWD', unit='t', cadence=10, url='https://www.fenghsin.com.tw/'),
    dict(id='jp_h2', name='日本 H2 廢鋼', group='steel', currency='JPY', unit='t', cadence=10, url='https://www.jisf.or.jp/'),
    dict(id='us_scrap', name='美國廢鋼', group='steel', currency='USD', unit='short_ton', cadence=35, url='https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information'),
    dict(id='comex_hg', name='COMEX HG 銅期貨', group='copper', currency='USD', unit='lb', cadence=4, url='https://www.cmegroup.com/markets/metals/base/copper.html'),
    dict(id='lme_cu', name='LME 銅', group='copper', currency='USD', unit='t', cadence=4, url='https://www.lme.com/en/metals/non-ferrous/lme-copper'),
    dict(id='shfe_cu', name='SHFE 上海銅期貨', group='copper', currency='CNY', unit='t', cadence=4, url='https://www.shfe.com.cn/'),
    dict(id='iron_ore', name='鐵礦砂／鋼鐵指標', group='steel', currency='USD', unit='t', cadence=45, url='https://www.worldbank.org/en/research/commodity-markets'),
    *[dict(id=f'fx_{c.lower()}', name=f'{c}/TWD 匯率', group='fx', currency='TWD', unit='fx', cadence=4, url='https://www.cbc.gov.tw/') for c in ('USD', 'JPY', 'CNY')],
]
INSTRUMENTS = {x['id']: x for x in CATALOG}
UNITS = {'t': 1, 'kg': 1000, 'lb': 1000 / 0.45359237, 'short_ton': 1000 / 907.18474}
CSV_FIELDS = ['instrument', 'date', 'price', 'currency', 'unit', 'specification', 'contract', 'source', 'source_url', 'license_note', 'delay', 'published_at']


class Quote(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False, extra='forbid')
    instrument: str
    date: date
    price: float = Field(gt=0, le=1e12)
    currency: Literal['TWD', 'USD', 'JPY', 'CNY']
    unit: Literal['t', 'kg', 'lb', 'short_ton', 'fx']
    specification: str = Field(min_length=1, max_length=200)
    contract: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=150)
    source_url: str = Field(min_length=1, max_length=1000)
    license_note: str = Field(min_length=1, max_length=500)
    delay: Literal['unknown', 'delayed', 'eod'] = 'unknown'
    published_at: datetime

    @field_validator('source_url')
    @classmethod
    def safe_url(cls, value):
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise ValueError('來源須為不含帳密的 http/https 網址')
        return value

    @model_validator(mode='after')
    def consistent(self):
        if self.instrument not in INSTRUMENTS:
            raise ValueError('未知追蹤項目')
        now = datetime.now(timezone.utc)
        if self.published_at.tzinfo is None:
            raise ValueError('來源發布時間須含時區，例如 +08:00')
        if self.published_at > now or self.date > now.astimezone(timezone(timedelta(hours=8))).date():
            raise ValueError('不能輸入未來行情或未來發布時間')
        if self.published_at.astimezone(timezone(timedelta(hours=8))).date() < self.date:
            raise ValueError('發布日期不得早於行情日期')
        if INSTRUMENTS[self.instrument]['group'] == 'fx':
            if self.unit != 'fx' or self.currency != 'TWD':
                raise ValueError('匯率須為一單位外幣兌 TWD')
        elif self.unit == 'fx':
            raise ValueError('金屬價格不可使用匯率單位')
        return self


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS market_quotes(
        id INTEGER PRIMARY KEY, instrument TEXT NOT NULL, date TEXT NOT NULL,
        price REAL NOT NULL CHECK(price>0), currency TEXT NOT NULL, unit TEXT NOT NULL,
        specification TEXT NOT NULL, contract TEXT NOT NULL, source TEXT NOT NULL,
        source_url TEXT NOT NULL, license_note TEXT NOT NULL, delay TEXT NOT NULL,
        published_at TEXT NOT NULL, updated_at TEXT NOT NULL, method TEXT NOT NULL,
        UNIQUE(instrument,date,specification,contract,currency,unit))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_market_date ON market_quotes(instrument,date)')
    c.execute('CREATE TABLE IF NOT EXISTS market_settings(name TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(2)')


def connection():
    import app
    return app.connect()


def key(row):
    return json.dumps([row[x] for x in ('instrument', 'specification', 'contract', 'currency', 'unit')], ensure_ascii=False, separators=(',', ':'))


def records():
    with connection() as c:
        return [dict(r) for r in c.execute('SELECT * FROM market_quotes ORDER BY date,id')]


def grouped():
    groups = defaultdict(list)
    for row in records():
        groups[key(row)].append(row)
    return groups


def insert_quotes(quotes, method):
    import sqlite3
    with connection() as c:
        try:
            for q in quotes:
                values = q.model_dump(mode='json')
                values['published_at'] = q.published_at.astimezone(timezone.utc).isoformat()
                values['updated_at'] = datetime.now(timezone.utc).isoformat()
                values['method'] = method
                cols = list(values)
                c.execute(f"INSERT INTO market_quotes({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", list(values.values()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, '同一日期、規格、合約與單位已有紀錄；整批未匯入，請先核對原紀錄') from exc
    return {'ok': True, 'imported': len(quotes)}


@router.get('/catalog')
def catalog():
    return {'instruments': CATALOG, 'csv_fields': CSV_FIELDS, 'automatic_sources': False}


@router.post('/quotes')
def add_quote(quote: Quote):
    return insert_quotes([quote], 'manual')


@router.get('/template')
def template():
    return Response('\ufeff' + ','.join(CSV_FIELDS) + '\r\n', media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="market-template.csv"'})


@router.post('/import')
async def import_csv(file: UploadFile = File(...)):
    try:
        content = await file.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise HTTPException(413, 'CSV 上限 2 MB')
        try:
            reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')), strict=True)
            if reader.fieldnames != CSV_FIELDS:
                raise ValueError('欄位順序或名稱不符，請下載範本')
            quotes = []
            for n, row in enumerate(reader, start=2):
                if n > 5001:
                    raise ValueError('一次最多匯入 5000 筆')
                try:
                    quotes.append(Quote(**row))
                except (ValueError, TypeError) as exc:
                    raise ValueError(f'第 {n} 列格式錯誤：{str(exc)[:300]}') from exc
            if not quotes:
                raise ValueError('沒有資料列；範本只有欄位，不含行情')
        except (ValueError, UnicodeError, csv.Error) as exc:
            raise HTTPException(400, str(exc)) from exc
        return insert_quotes(quotes, 'csv')
    finally:
        await file.close()


def change(latest, previous):
    return {'absolute': latest['price'] - previous['price'], 'percent': (latest['price'] / previous['price'] - 1) * 100, 'base_date': previous['date']}


def historical_change(rows, days):
    cutoff = date.fromisoformat(rows[-1]['date']) - timedelta(days=days)
    prior = [r for r in rows if date.fromisoformat(r['date']) <= cutoff]
    # Do not treat a much older quote as an exact 7/30 day observation.
    if not prior or (cutoff - date.fromisoformat(prior[-1]['date'])).days > 7:
        return None
    return change(rows[-1], prior[-1])


@router.get('/overview')
def overview():
    today = datetime.now(timezone(timedelta(hours=8))).date()
    series = []
    for series_key, rows in grouped().items():
        latest = rows[-1]
        series.append(dict(key=series_key, name=INSTRUMENTS[latest['instrument']]['name'], latest=latest,
            count=len(rows), previous=change(latest, rows[-2]) if len(rows)>1 else None,
            days7=historical_change(rows, 7), days30=historical_change(rows, 30),
            stale=(today-date.fromisoformat(latest['date'])).days > INSTRUMENTS[latest['instrument']]['cadence']))
    return {'series': series, 'tracked': CATALOG, 'generated_at': datetime.now(timezone.utc).isoformat()}


class Comparison(BaseModel):
    series: list[str] = Field(min_length=2, max_length=4)
    start: date
    end: date
    mode: Literal['index', 'twd'] = 'index'
    alignment: Literal['exact', 'asof'] = 'exact'
    max_age_days: int = Field(default=7, ge=0, le=14)
    lag: int = Field(default=0, ge=-30, le=30)


def at_or_before(rows, day, max_age):
    prior = [r for r in rows if r['date'] <= day]
    if not prior or (date.fromisoformat(day)-date.fromisoformat(prior[-1]['date'])).days > max_age:
        return None
    return prior[-1]


def convert(row, groups):
    if row['unit'] not in UNITS:
        return None
    fx = None
    rate = 1
    if row['currency'] != 'TWD':
        candidates = [values for values in groups.values() if values[0]['instrument'] == 'fx_' + row['currency'].lower()]
        # Multiple FX specifications must not be silently mixed.
        if len(candidates) != 1:
            return None
        fx = at_or_before(candidates[0], row['date'], 7)
        if not fx:
            return None
        rate = fx['price']
    return {'value': row['price'] * UNITS[row['unit']] * rate, 'fx_date': fx['date'] if fx else None,
            'fx_source': fx['source'] if fx else None, 'fx_source_url': fx['source_url'] if fx else None}


def correlation(pairs):
    if len(pairs) < 8:
        return None
    x, y = zip(*pairs)
    mx, my = mean(x), mean(y)
    denom = math.sqrt(sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))
    return sum((a-mx)*(b-my) for a,b in pairs)/denom if denom else None


@router.post('/compare')
def compare(query: Comparison):
    if query.start > query.end or (query.end-query.start).days > 3660:
        raise HTTPException(400, '日期範圍錯誤或超過十年')
    if len(set(query.series)) != len(query.series):
        raise HTTPException(400, '請選擇不同序列')
    groups = grouped()
    if any(k not in groups for k in query.series):
        raise HTTPException(400, '請先匯入並選擇有效序列')
    selected = [groups[k] for k in query.series]
    if query.mode == 'twd' and any(v[0]['unit']=='fx' for v in selected):
        raise HTTPException(400, '匯率不可轉換為台幣／公噸；請改用走勢指數')
    days = sorted({r['date'] for rows in selected for r in rows if query.start.isoformat() <= r['date'] <= query.end.isoformat()})
    points = []
    dropped = 0
    for day in days:
        entries = []
        for rows in selected:
            row = at_or_before(rows, day, query.max_age_days if query.alignment=='asof' else 0)
            if not row:
                break
            converted = convert(row, groups) if query.mode=='twd' else {'value': row['price'], 'fx_date': None}
            if not converted:
                break
            entries.append(dict(**converted, quote_date=row['date'], source=row['source'], source_url=row['source_url'], delay=row['delay'], published_at=row['published_at']))
        if len(entries)==len(selected):
            points.append({'date': day, 'entries': entries})
        else:
            dropped += 1
    if query.mode=='index' and points:
        bases = [e['value'] for e in points[0]['entries']]
        for point in points:
            for i, entry in enumerate(point['entries']):
                entry['value'] = entry['value']/bases[i]*100
    # Calendar-day lag; use only actual same-day observations, no carried data for inference.
    returns = []
    for rows in selected[:2]:
        values = {}
        for prev, row in zip(rows, rows[1:]):
            if query.start.isoformat() <= prev['date'] and row['date'] <= query.end.isoformat():
                values[(prev['date'],row['date'])] = row['price']/prev['price']-1
        returns.append(values)
    pairs=[]
    for (previous, day), value in returns[0].items():
        shifted = tuple((date.fromisoformat(d)+timedelta(days=query.lag)).isoformat() for d in (previous,day))
        if shifted in returns[1]:
            pairs.append((value, returns[1][shifted]))
    return {'points':points, 'dropped_dates':dropped, 'unit':'共同首日 = 100' if query.mode=='index' else 'TWD／公噸',
            'labels':[INSTRUMENTS[v[0]['instrument']]['name']+' · '+v[0]['specification']+' · '+v[0]['contract'] for v in selected],
            'correlation':correlation(pairs), 'correlation_samples':len(pairs), 'lag':query.lag,
            'warning':'不同規格、品質、稅別、交貨條件與合約不可視為同一商品；換算未含稅費、運費及品質調整。相關係數採原幣別報酬、相同觀察區間，不代表因果。正位移表示第一序列領先第二序列若干日。'}


class ResearchQuery(BaseModel):
    series: str


def direction(a, b):
    return 'up' if b>a else 'down' if b<a else 'flat'


def probabilities(labels):
    # Laplace-smoothed empirical direction frequencies, not calibrated probabilities.
    return {name:(labels.count(name)+1)/(len(labels)+3) for name in ('up','flat','down')}


@router.post('/research')
def research(query: ResearchQuery):
    rows = grouped().get(query.series, [])
    result = {'reliable':False, 'message':'無法可靠預測', 'observations':len(rows), 'backtest_samples':0,
              'probabilities':None, 'accuracy':None, 'flat_accuracy':None, 'interval':None, 'backtest':[]}
    if not rows or rows[0]['instrument'] != 'fh_scrap':
        return {**result, 'message':'無法可靠預測：請選擇豐興廢鋼歷史序列。'}
    if len(rows)<53:
        return {**result,'message':f'無法可靠預測：目前 {len(rows)} 筆，至少需要 53 筆同規格、同口徑的每週紀錄。持平週也須記錄。'}
    intervals=[(date.fromisoformat(b['date'])-date.fromisoformat(a['date'])).days for a,b in zip(rows,rows[1:])]
    if any(not 5<=gap<=9 for gap in intervals):
        return {**result,'message':'無法可靠預測：週資料間隔須為 5–9 日；缺週或混入日資料時，請先核對原始資料。'}
    if any(datetime.fromisoformat(r['published_at']).astimezone(timezone(timedelta(hours=8))).date()>date.fromisoformat(r['date'])+timedelta(days=3) for r in rows):
        return {**result,'message':'無法可靠預測：部分週資料發布較晚，不能作為當週已知資訊回測。'}
    labels=[direction(a['price'],b['price']) for a,b in zip(rows,rows[1:])]
    tests=[]
    for i in range(26,len(labels)):
        # At origin rows[i], only labels ending at/before that row are known.
        train=labels[:i]
        probs=probabilities(train)
        predicted=max(('flat','up','down'),key=lambda name:probs[name])
        tests.append({'origin':rows[i]['date'], 'target_date':rows[i+1]['date'],
                      'training_transitions':i, 'predicted':predicted, 'actual':labels[i], 'correct':predicted==labels[i]})
    accuracy=sum(t['correct'] for t in tests)/len(tests)
    n=len(tests); z=1.96
    center=(accuracy+z*z/(2*n))/(1+z*z/n)
    margin=z*math.sqrt(accuracy*(1-accuracy)/n+z*z/(4*n*n))/(1+z*z/n)
    today=datetime.now(timezone(timedelta(hours=8))).date()
    stale=(today-date.fromisoformat(rows[-1]['date'])).days>10
    return {**result, 'message':('無法可靠預測：最新資料已過期。以下僅為歷史基準研究。' if stale else '無法可靠預測：僅完成基準研究。歷史頻率未經校準，尚無獨立證據證明優於簡單基準。'),
            'probabilities':probabilities(labels), 'accuracy':accuracy,
            'flat_accuracy':sum(t['actual']=='flat' for t in tests)/n, 'backtest_samples':n,
            'interval':[max(0,center-margin),min(1,center+margin)], 'backtest':tests,
            'latest_date':rows[-1]['date'], 'source':rows[-1]['source'],
            'published_at':rows[-1]['published_at'], 'target_date':(date.fromisoformat(rows[-1]['date'])+timedelta(days=7)).isoformat(),
            'method':'擴展視窗、前 26 次週變化起訓；每期只使用已發生的方向。加一平滑頻率；比較永遠猜持平基準。95% Wilson 區間僅為歷史命中率區間，非價格預測區間；週資料可能自相關。',
            'stale':stale}


class RadarSettings(BaseModel):
    model_config=ConfigDict(allow_inf_nan=False)
    move: float=Field(default=5,ge=0.1,le=100)
    fx: float=Field(default=1,ge=0.1,le=100)
    streak: int=Field(default=3,ge=2,le=10)


@router.get('/radar-settings')
def radar_settings():
    with connection() as c:
        row=c.execute("SELECT value FROM market_settings WHERE name='radar'").fetchone()
    return RadarSettings.model_validate_json(row[0]).model_dump() if row else RadarSettings().model_dump()


@router.post('/radar')
def radar(settings: RadarSettings):
    with connection() as c:
        c.execute("INSERT INTO market_settings(name,value) VALUES('radar',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(settings.model_dump_json(),))
    groups=grouped(); signals=[]; skipped=0
    today=datetime.now(timezone(timedelta(hours=8))).date()
    def fresh(row):
        return (today-date.fromisoformat(row['date'])).days<=INSTRUMENTS[row['instrument']]['cadence']
    def emit(title,reason,rows):
        signals.append({'title':title,'reason':reason,'time':'；'.join(f"{r['date']} · {INSTRUMENTS[r['instrument']]['name']} · {r['specification']} · {r['contract']} · {r['source']} · {r['delay']} · 發布 {r['published_at']} · 更新 {r['updated_at']}" for r in rows)})
    for rows in groups.values():
        last=rows[-1]; item=INSTRUMENTS[last['instrument']]
        if not fresh(last):
            skipped+=1;continue
        if len(rows)<2:
            skipped+=1;continue
        prev=rows[-2]
        if (date.fromisoformat(last['date'])-date.fromisoformat(prev['date'])).days>item['cadence']:
            skipped+=1;continue
        move=(last['price']/prev['price']-1)*100
        name=item['name']+' · '+last['specification']+' · '+last['contract']
        if item['group']=='copper' and abs(move)>=settings.move:
            emit(name+' 波動提示',f'相鄰紀錄 {prev["date"]} → {last["date"]}，原幣報價變動 {move:+.2f}%，達門檻 {settings.move}%。請核對合約及延遲狀態。',[prev,last])
        if item['group']=='fx' and abs(move)>=settings.fx:
            emit(name+' 匯率風險',f'一單位外幣兌台幣變動 {move:+.2f}%，達門檻 {settings.fx}%；假設外幣金屬價不變，台幣換算價同方向變動，並非金屬原幣價格訊號。',[prev,last])
        count=settings.streak
        recent=rows[-count-1:]
        if len(recent)==count+1 and all((date.fromisoformat(b['date'])-date.fromisoformat(a['date'])).days<=item['cadence'] for a,b in zip(recent,recent[1:])):
            changes=[b['price']-a['price'] for a,b in zip(recent,recent[1:])]
            if all(x>0 for x in changes) or all(x<0 for x in changes):
                emit(name+' 連續走勢',f'連續 {count} 次觀察'+('上漲' if changes[0]>0 else '下跌')+'；按紀錄次數計算，不等於連續交易日。',[recent[0],last])
    taiwan=[r for r in groups.values() if r[0]['instrument']=='fh_scrap']
    japan=[r for r in groups.values() if r[0]['instrument']=='jp_h2']
    for tw in taiwan:
        for jp in japan:
            if not fresh(tw[-1]) or not fresh(jp[-1]):continue
            common=sorted(set(r['date'] for r in tw)&set(r['date'] for r in jp))
            if len(common)<2:continue
            start,end=common[-2:]
            if (date.fromisoformat(end)-date.fromisoformat(start)).days>14 or (today-date.fromisoformat(end)).days>10:continue
            t=[next(r for r in tw if r['date']==d) for d in (start,end)]
            j=[next(r for r in jp if r['date']==d) for d in (start,end)]
            a=(t[1]['price']/t[0]['price']-1)*100;b=(j[1]['price']/j[0]['price']-1)*100
            if a*b<0 and abs(a-b)>=settings.move:
                emit('日本／台灣廢鋼方向背離',f'{start} → {end}，豐興 {a:+.2f}%、日本 H2 {b:+.2f}%，差 {abs(a-b):.2f} 個百分點；原幣走勢、同日對齊，未調整品質、匯率與運費。',t+j)
    return {'signals':signals,'skipped':skipped,'message':f'本次沒有符合門檻的訊號。{skipped} 組資料不足、過期或間隔過長；沒有訊號不代表沒有風險。',
            'generated_at':datetime.now(timezone.utc).isoformat()}
