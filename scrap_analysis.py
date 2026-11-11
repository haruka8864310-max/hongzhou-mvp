"""Read-only adapters for current-snapshot charts and strict point-in-time research.

Historical charts are NOT historical information sets. Never use today's downloaded
snapshot in a past forecast. All originals, scopes and acquisition times survive.
"""
import calendar
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import market
import global_market as gm
import fenghsin

router = APIRouter(prefix='/api/scrap-analysis')
TZ = timezone(timedelta(hours=8))


def clock():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, '分析時間格式無效') from exc
    if stamp.tzinfo is None:
        raise HTTPException(422, '分析時間必須包含時區')
    return stamp.astimezone(timezone.utc)


def endday(day):
    return datetime.combine(date.fromisoformat(day), datetime.max.time(), TZ).astimezone(timezone.utc)


def period_end(period):
    if len(period) == 7:
        y, m = map(int, period.split('-'))
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    if len(period) == 4:
        return period + '-12-31'
    return period


def point(row, day, price, key, source_url=None):
    acquired = max(timestamp(row['retrieved_at']), timestamp(row.get('first_seen_at') or row['retrieved_at']))
    published = timestamp(row['published_at']) if row.get('published_at') else None
    known = max(acquired, published or acquired)
    return {'id': key, 'date': period_end(day), 'quote_date': day, 'price': price,
            'source': row.get('source', ''), 'source_url': source_url or row['source_url'],
            'published_at': row.get('published_at'), 'published_date': row.get('published_date'), 'known_at': known.isoformat(),
            'first_seen_at': row.get('first_seen_at') or row['retrieved_at'], 'retrieved_at': row['retrieved_at']}


def datasets(as_of=None):
    """No migration/copy. Monthly originals bypass captured duplicates in the ledger."""
    cutoff = timestamp(as_of) if as_of else clock()
    result = {}
    with market.connection() as c:
        facts = [dict(r) for r in c.execute('SELECT * FROM fenghsin_facts ORDER BY id')]
        monthly = [dict(r) for r in c.execute('''SELECT o.*,s.name,s.currency,s.unit,s.frequency,
            s.specification,s.source_url,s.provider FROM metal_observations o
            JOIN metal_series s ON s.id=o.series_id ORDER BY o.id''')]
    fh_groups = defaultdict(list)
    for r in facts:
        if r['price'] is None:
            continue
        identity = [r[k] for k in ('product', 'source', 'grade', 'tax_basis', 'price_basis')]
        key = 'fh:' + r['product'] + ':' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:12]
        p = point(r, r['announcement_date'], r['price'], 'fh:' + str(r['id']))
        if timestamp(p['known_at']) > cutoff or p['date'] > cutoff.astimezone(TZ).date().isoformat():
            continue
        fh_groups[(key, p['date'])].append((r, p))
    for (key, day), values in fh_groups.items():
        r, p = values[-1]
        s = result.setdefault(key, {'id': key, 'market': 'tw_' + r['product'], 'country': 'TW',
            'name': '豐興' + fenghsin.PRODUCTS[r['product']], 'scope': r['grade']+'／稅別'+r['tax_basis']+'／'+r['price_basis'],
            'currency': 'TWD', 'unit': 't', 'frequency': 'weekly', 'origin': 'fenghsin_facts', 'points': [], 'conflicts': []})
        if len({v[1]['price'] for v in values}) > 1:
            s['conflicts'].append(day)
        else:
            s['points'].append(p)
    for r in gm.available(cutoff.isoformat()):
        if r['origin'].startswith('metal:') or r['price'] is None:
            continue
        p = point(r, r['quote_date'], r['price'], 'global:'+str(r['id']), r.get('document_url'))
        if p['date'] > cutoff.astimezone(TZ).date().isoformat():
            continue
        key = 'g:' + r['series_id']
        nbs=(r['origin'].startswith(('archive-nbs|','nbs|')) and r['source']=='中國國家統計局')
        if nbs:
            # A document URL is provenance, not a new economic instrument.
            # Both fixed-scope NBS connectors parse the identical official
            # specification; preserve each point's URL and acquisition clock.
            identity=[r[k] for k in ('market','country','grade','currency','unit','frequency','source')]
            key='nbs:'+hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
        s = result.setdefault(key, {'id': key, 'market': r['market'], 'country': r['country'],
            'name': r['instrument'], 'scope': r['grade']+'／'+r['delivery_term'],
            'currency': r['currency'], 'unit': r['unit'], 'frequency': r['frequency'],
            'origin': 'global_quotes', 'points': [], 'conflicts': []})
        if nbs:
            if p['date'] in s['conflicts']:continue
            existing=next((q for q in s['points'] if q['date']==p['date']),None)
            if existing:
                if existing['price']!=p['price']:
                    s['conflicts'].append(p['date']);s['points']=[q for q in s['points'] if q['date']!=p['date']]
                continue
        s['points'].append(p)
    for r in monthly:
        key = 'm:' + r['series_id']
        r['source'] = r['provider']
        p = point(r, r['period'], r['value'], 'metal:'+str(r['id']))
        if timestamp(p['known_at']) > cutoff or p['date'] > cutoff.astimezone(TZ).date().isoformat():
            continue
        s = result.setdefault(key, {'id': key, 'market': r['series_id'], 'country': 'TW' if r['provider'].startswith('tw_') else 'US' if r['provider']=='bls' else 'GLOBAL',
            'name': r['name'], 'scope': r['specification'], 'currency': r['currency'],
            'unit': r['unit'].split('/',1)[1] if r['unit'].startswith(r['currency']+'/') else r['unit'],
            'frequency': 'monthly', 'origin': 'metal_observations', 'points': [], 'conflicts': []})
        # SQL insertion order chooses the latest version available at this cutoff.
        s['points'] = [old for old in s['points'] if old['date'] != p['date']] + [p]
    for s in result.values():
        s['points'].sort(key=lambda p: p['date'])
    return {k:s for k,s in result.items() if s['points']}


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    a: str = Field(default='', max_length=100)
    b: str = Field(default='', max_length=100)
    months: int = Field(default=12, ge=0, le=120)
    mode: str = Field(default='index', pattern='^(native|twd|index)$')
    max_age_days: int = Field(default=35, ge=7, le=120)


@router.get('/settings')
def settings():
    with market.connection() as c:
        row = c.execute("SELECT value FROM market_settings WHERE name='scrap_analysis'").fetchone()
    return Preferences.model_validate_json(row[0]).model_dump() if row else Preferences().model_dump()


@router.put('/settings')
def save_settings(value: Preferences):
    with market.connection() as c:
        c.execute("INSERT INTO market_settings(name,value) VALUES('scrap_analysis',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (value.model_dump_json(),))
    return value.model_dump()


@router.get('/catalog')
def catalog(as_of: str | None = None):
    data = datasets(as_of)
    items = [{k:v for k,v in s.items() if k != 'points'} | {'count': len(s['points']),
             'earliest': s['points'][0]['date'], 'latest': s['points'][-1]['date']} for s in data.values()]
    items.sort(key=lambda s: (0 if s['origin']=='fenghsin_facts' else 1 if s['market']=='jp_tokyo' else 2, s['name'], s['scope']))
    a = next((s['id'] for s in items if s['origin']=='fenghsin_facts' and s['market']=='tw_scrap'), '')
    b = next((s['id'] for s in items if s['market']=='jp_tokyo' and '岡山工場／二級' in s['scope']), '')
    b = b or next((s['id'] for s in items if s['market']=='jp_tokyo'), '')
    return {'items': items, 'defaults': {'a': a, 'b': b}, 'reason': '' if a and b else '缺少豐興絕對價格或東京製鐵歷史，請先更新來源；不以其他商品替代。'}


@router.get('/summary')
def summary():
    import japan_sources
    data=datasets()
    preferred=catalog()['defaults']['b']
    chosen=[preferred]+[next((key for key,s in data.items() if s['market'] in markets),'')
        for markets in [('us_index','bls_steel_scrap'),('wb_iron_ore',),('fx_usd',),('fx_jpy',)]]
    rows=[]
    for key in chosen:
        if key not in data:continue
        s=data[key];last=s['points'][-1];prev=s['points'][-2] if len(s['points'])>=2 else None
        rows.append({'id':key,'name':s['name'],'scope':s['scope'],'currency':s['currency'],
            'unit':s['unit'],'frequency':s['frequency'],'latest':last,
            'previous_date':prev['quote_date'] if prev else None,
            'change_percent':(last['price']/prev['price']-1)*100 if prev else None,
            'age_days':(clock().astimezone(TZ).date()-date.fromisoformat(last['date'])).days,
            'source_error':japan_sources.state().get('error') if s['market']=='jp_tokyo' else None})
    return {'items':rows,'note':'只列本機已有資料的代表指標；美國PPI是指數，鐵礦砂是輔助原料，不是HMS成交價。漲跌以各自前一筆實際觀察計算，月份與週盤不同步。'}


def latest(points, day, max_age=35, origin=None):
    candidates = [p for p in points if p['date'] <= day and
                  (origin is None or timestamp(p['known_at']) <= origin)]
    if not candidates:
        return None
    p = max(candidates, key=lambda p: (p['date'], p['known_at']))
    age = (date.fromisoformat(day) - date.fromisoformat(p['date'])).days
    return {**p, 'age_days': age} if age <= max_age else None


def monday(day):
    d = date.fromisoformat(day)
    return d - timedelta(days=d.weekday())


def week_rows(series):
    groups = {}
    for p in series['points']:
        groups[monday(p['date']).isoformat()] = p
    return groups


def as_twd(p, series, data, cutoff):
    if series['currency'] == 'TWD' and not series['unit'].startswith('TWD per'):
        return {**p, 'value': p['price'], 'fx': None}, None
    market_id = {'JPY':'fx_jpy', 'USD':'fx_usd', 'CNY':'fx_cny'}.get(series['currency'])
    if not market_id:
        return None, '指數、產量或此幣別不能換算為台幣價格'
    groups = [s for s in data.values() if s['market']==market_id and s['frequency']=='daily']
    preferred = [s for s in groups if any('cpx.cbc.gov.tw' in p['source_url'] for p in s['points'])]
    groups = preferred or groups
    if len(groups) != 1:
        return None, '缺少唯一同口徑'+series['currency']+'/TWD日參考匯率'
    fx = latest(groups[0]['points'], p['date'], 7)
    if not fx:
        return None, '缺少報價日以前7日內的'+series['currency']+'/TWD匯率，不用今日匯率回填'
    return {**p, 'value': p['price']*fx['price'], 'fx': fx}, None


@router.get('/compare')
def compare(a: str='', b: str='', mode: str='index', months: int=12,
            max_age_days: int=35, as_of: str | None=None):
    try:
        Preferences(a=a, b=b, mode=mode, months=months, max_age_days=max_age_days)
    except ValidationError as exc:
        raise HTTPException(422, '比較方式、期間或資料年齡門檻無效') from exc
    data = datasets(as_of)
    defaults = catalog(as_of)['defaults']
    a, b = a or defaults['a'], b or defaults['b']
    if a not in data or b not in data:
        raise HTTPException(422, '缺少指定序列：請先更新豐興與東京製鐵，或重新選擇目前有資料的指標')
    if a == b:
        raise HTTPException(422, '請選擇兩個不同市場序列')
    left, right = data[a], data[b]
    end = (timestamp(as_of) if as_of else clock()).astimezone(TZ).date()
    start = end - timedelta(days=round(months*30.4375)) if months else date.min
    curves, errors = {}, {}
    for key, s in [('a', left), ('b', right)]:
        points, missing = [], []
        for p in s['points']:
            if not start.isoformat() <= p['date'] <= end.isoformat():
                continue
            converted, reason = as_twd(p, s, data, end) if mode=='twd' else ({**p, 'value':p['price'], 'fx':None}, None)
            if converted:
                points.append(converted)
            else:
                missing.append({'quote_date':p['quote_date'], 'reason':reason})
        curves[key] = points
        errors[key] = missing
    aligned = []
    # Display-only historical snapshots: last observed values at actual A weeks.
    # No synthetic daily rows and NOT a historical forecasting information set.
    for week, original in week_rows(left).items():
        day = (date.fromisoformat(week)+timedelta(days=6)).isoformat()
        if not start.isoformat() <= day <= end.isoformat():
            continue
        pa = latest(curves['a'], day, max_age_days)
        pb = latest(curves['b'], day, max_age_days)
        if pa and pb:
            aligned.append({'date':day, 'a':pa['value'], 'b':pb['value'], 'a_observation':pa, 'b_observation':pb})
    if mode == 'index':
        if aligned:
            base = aligned[0]
            for key in ('a', 'b'):
                base_point = base[key+'_observation']
                curves[key] = [{**p, 'value':p['price']/base[key]*100} for p in curves[key] if p['date']>=base_point['date']]
            aligned = [{**p, 'a':p['a']/base['a']*100, 'b':p['b']/base['b']*100} for p in aligned]
        else:
            curves = {'a':[], 'b':[]}
    def summary(s):
        ps=s['points'];change=(ps[-1]['price']/ps[-2]['price']-1)*100 if len(ps)>1 else None
        return {k:v for k,v in s.items() if k!='points'} | {'latest':ps[-1], 'count':len(ps), 'change_percent':change,
                 'age_days':(end-date.fromisoformat(ps[-1]['date'])).days}
    return {'a':summary(left), 'b':summary(right), 'curves':curves, 'aligned':aligned, 'missing':errors,
            'mode':mode, 'as_of':(timestamp(as_of) if as_of else clock()).isoformat(),
            'reason': '已有兩個市場的實際歷史曲線。' if all(len(p)>=2 for p in curves.values()) else '缺少此期間的兩筆有效觀察、共同基期或歷史匯率；請查看缺值原因或改原幣模式。',
            'warning':'目前可得快照的歷史比較，非當年可得資訊回測。週對齊只引用該週末以前的最後觀察，列出報價日與年齡；不是新增日價。月資料以月底代表觀察期，發布不明者不作當時已公開的推論。原幣圖為雙軸；換成台幣仍不消除品級、稅別與交貨差異，不計算跨市場套利價差。'}


def validate(c):
    row=c.execute("SELECT value FROM market_settings WHERE name='scrap_analysis'").fetchone()
    if row:
        Preferences.model_validate_json(row[0])


@router.get('/research')
@router.get('/predict')
def research(a:str='',b:str='',months:int=12,max_age_days:int=35,as_of:str|None=None):
    import scrap_forecast
    try:
        return scrap_forecast.research(a,b,months,max_age_days,as_of)
    except ValidationError as exc:
        raise HTTPException(422, '研究期間或資料年齡門檻無效') from exc
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
