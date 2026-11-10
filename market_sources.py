"""Allowlisted official/public reference feeds. No scraping, secrets or paid APIs."""
import json
import math
import os
import threading
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
import market

TAIPEI = timezone(timedelta(hours=8))
CBC_URL = 'https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01'
FRANKFURTER_URL = 'https://api.frankfurter.dev/v2/rates'
UPDATE_LOCK = threading.Lock()
AUTO_IDS = ['fx_usd', 'fx_jpy', 'fx_cny']

SOURCE_REVIEW = {
    'comex_hg': ('日內／日結算', '需確認 CME 資料授權；免費瀏覽不等於公司顯示／分析使用權。歷史產品另有條件。', 'https://www.cmegroup.com/market-data/license-data.html'),
    'lme_cu': ('日內／日結算', 'LME 即時、延遲、歷史及非顯示使用有不同授權，未確認免費公司用自動來源。', 'https://www.lme.com/en/about/faqs/market-data-faqs'),
    'shfe_cu': ('交易日／合約', 'SHFE 資訊有商業使用限制，未取得適用許可；不抓取延時頁面。', 'https://www.shfe.com.cn/disclaimer/'),
    'fh_scrap': ('每週公告', '首頁豐興價格中心已另外保存公開數值表；為供應商轉述，品級／稅別未載明，不混入此舊序列。', 'https://juyi-steel.tw/steel-price-trends/'),
    'fh_rebar': ('每週公告', '首頁價格中心可自動取得公開數值事實，與月均及此舊手動序列分開；並非整站再利用授權。', 'https://juyi-steel.tw/steel-price-trends/'),
    'jp_h2': ('月度／地區區間', 'JISRI區間註明第三方日刊市況通信社，未確認再利用；東京製鐵自刊原品級另由日本連接器保存，不改稱H2或FOB。', 'https://www.jisri.or.jp/market'),
    'us_scrap': ('依指標，日／月／年', 'USGS 自製資料可開放利用，但年報統計不是即時廢鋼價；未導入不相同的替代品。', 'https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information'),
    'iron_ore': ('月／年背景資料', 'World Bank 個別資料集及第三方欄位須核對；本次未實作此來源。', 'https://www.worldbank.org/en/research/commodity-markets'),
}


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    tracked_markets: list[str] = Field(default_factory=lambda: list(market.INSTRUMENTS), min_length=1)
    auto_update: bool = True
    update_minutes: int = Field(default=360, ge=60, le=1440)
    analysis_days: int = Field(default=365, ge=30, le=1825)
    anomaly_percent: float = Field(default=5, ge=0.1, le=100)
    compare_markets: list[str] = Field(default_factory=lambda: ['fx_usd', 'fx_jpy'], min_length=2, max_length=2)
    provider: str = 'cbc'

    @field_validator('tracked_markets', 'compare_markets')
    @classmethod
    def valid_ids(cls, values):
        if len(set(values)) != len(values) or any(x not in market.INSTRUMENTS for x in values):
            raise ValueError('市場代碼無效或重複')
        return values

    @field_validator('provider')
    @classmethod
    def valid_provider(cls, value):
        if value not in ('cbc', 'frankfurter'):
            raise ValueError('僅支援已查核來源')
        return value


def migrate(c):
    columns = {r['name'] for r in c.execute('PRAGMA table_info(market_quotes)')}
    for name, sql in [('publication_known', 'INTEGER NOT NULL DEFAULT 1'), ('source_updated_at', 'TEXT')]:
        if name not in columns:
            c.execute(f'ALTER TABLE market_quotes ADD COLUMN {name} {sql}')
    c.execute('''CREATE TABLE IF NOT EXISTS market_source_status(
        instrument TEXT NOT NULL, provider TEXT NOT NULL, checked_at TEXT,
        success_at TEXT, latest_date TEXT, source_updated_at TEXT,
        error TEXT, imported INTEGER NOT NULL DEFAULT 0, conflicts INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(instrument,provider))''')
    c.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(3)')


def get_settings():
    with market.connection() as c:
        row = c.execute("SELECT value FROM market_settings WHERE name='application'").fetchone()
    return Settings.model_validate_json(row[0]) if row else Settings()


@market.router.get('/settings')
def settings_get():
    return get_settings().model_dump()


@market.router.put('/settings')
def settings_save(settings: Settings):
    if any(x not in settings.tracked_markets for x in settings.compare_markets):
        raise HTTPException(400, '比較項目必須包含在追蹤市場內')
    with market.connection() as c:
        c.execute("INSERT INTO market_settings(name,value) VALUES('application',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (settings.model_dump_json(),))
    return settings.model_dump()


def fetch_json(url):
    # URLs are constructed only from these fixed providers, never arbitrary UI URLs.
    if not (url == CBC_URL or url.startswith(FRANKFURTER_URL+'?')):
        raise ValueError('未核准來源')
    request = Request(url, headers={'User-Agent': 'HongzhouLocalMarket/0.3', 'Accept': 'application/json'})
    with urlopen(request, timeout=20) as response:
        body = response.read(16 * 1024 * 1024 + 1)
    if len(body) > 16 * 1024 * 1024:
        raise ValueError('來源檔案超過上限')
    return json.loads(body.decode('utf-8-sig'))


def number(value):
    if str(value).strip() in ('-', '', 'None'):
        return None
    value = float(value)
    if not math.isfinite(value) or not 0 < value <= 1e12:
        raise ValueError('來源數值超出範圍')
    return value


def parse_cbc(payload, cutoff):
    """Resolve column names; never assume positions or use a different quote day."""
    table = payload['data']
    columns = [r['data'].strip() for r in table['structure']['Table1']]
    indices = {}
    for currency, suffix in [('TWD', 'NTD/USD'), ('JPY', 'JPY/USD'), ('CNY', 'CNY/USD')]:
        matches = [i+1 for i, text in enumerate(columns) if text.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError('央行欄位已改變')
        indices[currency] = matches[0]
    observations = []
    for row in table['dataSets']:
        day = datetime.strptime(row[0], '%Y%m%d').date()
        if day < cutoff:
            continue
        if day > datetime.now(TAIPEI).date():
            raise ValueError('來源包含未來日期')
        twd = number(row[indices['TWD']])
        if twd is None:
            continue
        observations.append(('fx_usd', day.isoformat(), twd))
        for currency in ('JPY', 'CNY'):
            rate = number(row[indices[currency]])
            if rate:
                observations.append(('fx_'+currency.lower(), day.isoformat(), twd/rate))
    return observations, payload['meta'].get('last_updated')


def parse_frankfurter(payload, cutoff):
    if not isinstance(payload, list):
        raise ValueError('Frankfurter 回應格式不符')
    by_date = {}
    for row in payload:
        if row['base'] != 'USD' or row['quote'] not in ('TWD', 'JPY', 'CNY'):
            raise ValueError('非預期匯率方向')
        day = date.fromisoformat(row['date'])
        if day > datetime.now(TAIPEI).date():
            raise ValueError('來源包含未來日期')
        if day >= cutoff:
            by_date.setdefault(row['date'], {})[row['quote']] = number(row['rate'])
    observations = []
    for day, rates in sorted(by_date.items()):
        if not rates.get('TWD'):
            continue
        observations.append(('fx_usd', day, rates['TWD']))
        for currency in ('JPY', 'CNY'):
            if rates.get(currency):
                observations.append(('fx_'+currency.lower(), day, rates['TWD']/rates[currency]))
    return observations, None


def provider_info(provider):
    if provider == 'cbc':
        return {'name':'中央銀行 BP01D01 日參考匯率', 'url':CBC_URL,
                'spec':'央行 16:00 研究參考／交叉換算', 'contract':'日參考（非成交價）',
                'license':'政府資料開放授權條款第1版；來源 data.gov.tw/dataset/6563；限國內研究參考，非銀行買賣成交價'}
    return {'name':'Frankfurter 混合日參考匯率', 'url':FRANKFURTER_URL,
            'spec':'Frankfurter 混合參考／交叉換算', 'contract':'日參考（非成交價）',
            'license':'https://frankfurter.dev/ 商業使用與本機歷史快取說明；混合參考值非官方／可成交匯率；另見 /license/'}


def synchronize(force=True):
    if not UPDATE_LOCK.acquire(blocking=False):
        raise HTTPException(409, '來源正在更新，請稍後重新整理')
    try:
        settings = get_settings()
        ids = [x for x in AUTO_IDS if x in settings.tracked_markets]
        if not ids:
            return {'ok':True, 'message':'未追蹤可自動更新的匯率市場', 'imported':0}
        provider = settings.provider
        now = datetime.now(timezone.utc)
        with market.connection() as c:
            attempts = [r[0] for r in c.execute('SELECT checked_at FROM market_source_status WHERE provider=? AND instrument IN (%s)' % ','.join('?' for _ in ids), [provider, *ids]) if r[0]]
        # Respect frequency across restarts; manual requests still have a one-minute cooldown.
        limit = 60 if force else settings.update_minutes * 60
        if len(attempts) == len(ids) and (now-min(datetime.fromisoformat(v) for v in attempts)).total_seconds() < limit:
            return {'ok':True, 'message':'尚未到下次更新時間；手動更新至少間隔一分鐘', 'imported':0}
        cutoff = datetime.now(TAIPEI).date()-timedelta(days=settings.analysis_days+30)
        info = provider_info(provider)
        try:
            if provider == 'cbc':
                observations, source_updated = parse_cbc(fetch_json(CBC_URL), cutoff)
            else:
                url = FRANKFURTER_URL+'?base=USD&quotes=TWD,JPY,CNY&from='+cutoff.isoformat()+'&to='+datetime.now(TAIPEI).date().isoformat()
                observations, source_updated = parse_frankfurter(fetch_json(url), cutoff)
            now = datetime.now(timezone.utc)
            valid = {}
            for instrument, day, value in observations:
                if instrument not in ids:
                    continue
                q = market.Quote(instrument=instrument, date=day, price=value, currency='TWD', unit='fx',
                    specification=info['spec'], contract=info['contract'], source=info['name'], source_url=info['url'],
                    license_note=info['license'], delay='delayed', published_at=now)
                identity = (instrument,day)
                if identity in valid and valid[identity].price != q.price:
                    raise ValueError('來源同日資料互相衝突')
                valid[identity] = q
            if any(not any(k[0]==x for k in valid) for x in ids):
                raise ValueError('所選期間缺少追蹤幣別資料；未寫入不完整結果')
            total = 0
            conflicts = 0
            with market.connection() as c:
                for instrument in ids:
                    added = 0; changed = 0
                    quotes = [v for (i,d),v in valid.items() if i==instrument]
                    for q in quotes:
                        import global_market
                        global_market.record_fx(c,q,provider,now.isoformat())
                        row = c.execute('SELECT price FROM market_quotes WHERE instrument=? AND date=? AND specification=? AND contract=? AND currency=? AND unit=?',
                            (q.instrument,q.date.isoformat(),q.specification,q.contract,q.currency,q.unit)).fetchone()
                        if row:
                            if not math.isclose(row['price'],q.price,rel_tol=1e-10): changed += 1
                            continue  # Never overwrite a prior quote, including manual/imported data.
                        values = q.model_dump(mode='json')
                        values.update(updated_at=now.isoformat(), method='auto:'+provider, publication_known=0, source_updated_at=source_updated)
                        cols=list(values)
                        c.execute(f"INSERT INTO market_quotes({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",list(values.values()))
                        added+=1
                    latest=max(q.date for q in quotes).isoformat()
                    c.execute('''INSERT INTO market_source_status(instrument,provider,checked_at,success_at,latest_date,source_updated_at,error,imported,conflicts)
                        VALUES(?,?,?,?,?,?,NULL,?,?) ON CONFLICT(instrument,provider) DO UPDATE SET
                        checked_at=excluded.checked_at,success_at=excluded.success_at,latest_date=excluded.latest_date,
                        source_updated_at=excluded.source_updated_at,error=NULL,imported=excluded.imported,conflicts=excluded.conflicts''',
                        (instrument,provider,now.isoformat(),now.isoformat(),latest,source_updated,added,changed))
                    total+=added;conflicts+=changed
            return {'ok':True,'imported':total,'conflicts':conflicts,'message':f'已完成來源檢查，新增 {total} 筆；{conflicts} 筆來源修訂保留舊值待核對。報價日期與下載成功分別顯示，不代表即時。'}
        except (ValueError, KeyError, TypeError, IndexError, URLError, TimeoutError, OSError) as exc:
            # Do not replace prior quotes or last successful timestamp on failure.
            detail = f'{type(exc).__name__}: {str(exc)[:220]}'
            with market.connection() as c:
                for instrument in ids:
                    c.execute('''INSERT INTO market_source_status(instrument,provider,checked_at,error) VALUES(?,?,?,?)
                        ON CONFLICT(instrument,provider) DO UPDATE SET checked_at=excluded.checked_at,error=excluded.error''',
                        (instrument,provider,now.isoformat(),detail))
            return {'ok':False,'imported':0,'message':'來源無法取得；已保留既有資料。'+detail}
    finally:
        UPDATE_LOCK.release()


@market.router.post('/sync')
def sync_now():
    return synchronize(force=True)


@market.router.get('/sources')
def source_status():
    settings = get_settings()
    with market.connection() as c:
        stored = {r['instrument']:dict(r) for r in c.execute('SELECT * FROM market_source_status WHERE provider=?',(settings.provider,))}
    result = []
    today = datetime.now(TAIPEI).date()
    for item in market.CATALOG:
        enabled = item['id'] in settings.tracked_markets
        row = stored.get(item['id'],{})
        if item['id'] in AUTO_IDS:
            info = provider_info(settings.provider)
            status = 'unavailable' if row.get('error') else 'pending' if not row.get('success_at') else 'delayed' if (today-date.fromisoformat(row['latest_date'])).days>4 else 'success'
            result.append({**row,'instrument':item['id'],'name':item['name'],'enabled':enabled,'automatic':True,'status':status,
                'source_name':info['name'],'url':info['url'],'frequency':'交易日日參考；來源整理與發布可能較晚',
                'license':info['license'],'api_key':'不需要','history':'央行回傳自1993年起歷史；本機保存所選分析期間加30日緩衝' if settings.provider=='cbc' else '依幣別及提供者覆蓋；本機保存所選分析期間加30日緩衝',
                'reason':'非即時／非成交價；JPY/TWD 與 CNY/TWD 以同日 USD 交叉換算。來源未提供逐筆發布時間。'})
        else:
            frequency, reason, url = SOURCE_REVIEW[item['id']]
            result.append({'instrument':item['id'],'name':item['name'],'enabled':enabled,'automatic':False,'status':'manual',
                'source_name':'尚無適用的免費自動連接器','url':url,'frequency':frequency,'api_key':'未接入；憑證或許可依供應商',
                'history':'依供應商及授權；本版接受有權使用的手動／CSV 歷史','license':'未確認可免費自動公司使用','reason':reason})
    return {'sources':result,'settings':settings.model_dump(),'checked_at':datetime.now(timezone.utc).isoformat()}


def start_scheduler():
    stop = threading.Event()
    if os.getenv('HONGZHOU_DISABLE_SCHEDULER') == '1':
        return stop,None
    def run():
        while not stop.is_set():
            try:
                if get_settings().auto_update:
                    import fenghsin
                    fenghsin.sync(force=False)
                    synchronize(force=False)
                    import metal_engine
                    metal_engine.sync(force=False)
                    import global_market
                    global_market.capture_existing()
                    import japan_sources
                    japan_sources.sync(force=False)
            except Exception:
                # A busy manual update or transient restore must not terminate the worker.
                import logging
                logging.getLogger(__name__).exception('Market update worker failed')
            stop.wait(30)
    thread=threading.Thread(target=run,name='hongzhou-reference-rates',daemon=True)
    thread.start()
    return stop,thread
