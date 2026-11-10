"""Bounded public numeric facts, not copies of news, commentary or page layouts.

Independent publishers are retained separately. A consistent adjustment does not
independently verify an absolute price. Unknown tax/grade is never filled in.
"""
import hashlib
import json
import re
import ssl
import threading
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

import certifi
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix='/api/fenghsin')
LOCK = threading.Lock()
AGENT = 'HongzhouMarketResearch/1.0'
SOURCES = {
    'juyi': ('聚毅鋼鐵公開數值表', 'https://juyi-steel.tw/steel-price-trends/'),
    'double': ('双享鋼鐵公開調價表', 'https://www.double-steel.com/zh/market'),
}
PRODUCTS = {'scrap': '廢鋼收購價', 'rebar': '鋼筋基價', 'sections': '型鋼基價'}
FACT_USE = '僅擷取公開日期及數值事實供本機研究；非整站授權，不儲存新聞、評論及版面。品級與稅別未載明，尚非獨立驗證之官方成交價。'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS fenghsin_facts(
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
        announcement_date TEXT NOT NULL, product TEXT NOT NULL,
        price REAL CHECK(price>0), adjustment REAL, currency TEXT NOT NULL DEFAULT 'TWD',
        unit TEXT NOT NULL DEFAULT '元／公噸', grade TEXT NOT NULL,
        tax_basis TEXT NOT NULL, price_basis TEXT NOT NULL,
        published_at TEXT, first_seen_at TEXT NOT NULL, retrieved_at TEXT NOT NULL,
        kind TEXT NOT NULL, verification TEXT NOT NULL,
        fingerprint TEXT UNIQUE NOT NULL, CHECK(price IS NOT NULL OR adjustment IS NOT NULL))''')
    c.execute('''CREATE TABLE IF NOT EXISTS fenghsin_source_status(
        source TEXT PRIMARY KEY, checked_at TEXT NOT NULL, success_at TEXT,
        error TEXT, observed_count INTEGER NOT NULL DEFAULT 0,
        inserted_count INTEGER NOT NULL DEFAULT 0, latest_date TEXT)''')
    c.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(6)')


def fetch(url):
    allowed = [u for _, u in SOURCES.values()]
    allowed += [f'https://{urlsplit(u).netloc}/robots.txt' for u in allowed]
    if url not in allowed:
        raise ValueError('拒絕非核准網址；不追蹤文章或隱藏端點')
    context = ssl.create_default_context()
    context.load_verify_locations(certifi.where())
    with urlopen(Request(url, headers={'User-Agent': AGENT}), timeout=12, context=context) as response:
        if response.status != 200 or urlsplit(response.url).netloc != urlsplit(url).netloc:
            raise ValueError('來源未完整回應 HTTP200 或轉至其他網站')
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('來源超過2MB，停止取得')
    return raw


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.table, self.row, self.cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.table = []
        elif tag == 'tr' and self.table is not None:
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = {'text': '', 'href': None}
        elif tag == 'a' and self.cell is not None:
            self.cell['href'] = dict(attrs).get('href')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def parse(source, raw):
    parser = Tables()
    parser.feed(raw.decode('utf-8-sig'))
    tables = [t for t in parser.tables if t and len(t[0]) >= 4
              and all(w in ''.join(c['text'] for c in t[0]) for w in ('日期', '廢鋼', '鋼筋', '型鋼'))]
    if len(tables) != 1:
        raise ValueError('找不到唯一豐興三品項表；未猜測欄位')
    header = [re.sub(r'\s+', '', c['text']) for c in tables[0][0]][:4]
    if not all(word in header[i] for i, word in enumerate(('日期', '廢鋼', '鋼筋', '型鋼'))):
        raise ValueError('來源欄位順序變更')
    rows, seen = [], set()
    today = datetime.now(timezone(timedelta(hours=8))).date()
    for cells in tables[0][1:]:
        if len(cells) < 4:
            raise ValueError('價格列不完整')
        text = re.sub(r'\s+', '', cells[0]['text'])
        if not re.fullmatch(r'\d{4}/\d{1,2}/\d{1,2}', text):
            raise ValueError('日期格式不符，拒絕靜默跳過公告')
        day = date(*map(int, text.split('/')))
        if day > today or day in seen:
            raise ValueError('未來日期或重複公告日期，停止更新')
        seen.add(day)
        link = urljoin(SOURCES[source][1], cells[0]['href'] or SOURCES[source][1])
        if urlsplit(link).netloc != urlsplit(SOURCES[source][1]).netloc:
            raise ValueError('公告來源連結不符')
        for product, cell in zip(PRODUCTS, cells[1:4]):
            value = re.sub(r'\s+', '', cell['text']).replace(',', '')
            price, change = None, None
            if source == 'juyi':
                if not re.fullmatch(r'\d+(?:\.\d+)?', value):
                    raise ValueError('絕對價格不是單一數值')
                price = float(value)
                if not 100 <= price <= 200000:
                    raise ValueError('價格超出合理元／公噸範圍')
            elif value == '平盤':
                change = 0.0
            else:
                match = re.fullmatch(r'(上漲|下跌)(\d+)', value)
                if not match:
                    raise ValueError('調價不是明確上漲／下跌／平盤')
                change = float(match[2]) * (1 if match[1] == '上漲' else -1)
            rows.append({'source': source, 'source_url': link, 'announcement_date': day.isoformat(),
                         'product': product, 'price': price, 'adjustment': change,
                         'grade': '來源未載明', 'tax_basis': '來源未載明',
                         'price_basis': PRODUCTS[product] + '／豐興週盤（供應商轉述）',
                         'published_at': None, 'kind': 'direct' if price else 'change_only',
                         'verification': 'unverified'})
    if not rows:
        raise ValueError('沒有任何有效價格或調價列')
    return rows


def sync(force=True):
    import app
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, '豐興來源更新中，請稍候')
    results = []
    try:
        for source, (name, url) in SOURCES.items():
            with app.connect() as c:
                state = c.execute('SELECT * FROM fenghsin_source_status WHERE source=?', (source,)).fetchone()
            # Bounded polling: never crawl archives or article pages. Monday and midweek.
            hours = 2 if datetime.now(timezone(timedelta(hours=8))).weekday() == 0 else 6
            cooldown = 60 if force else hours * 3600
            if state and (datetime.now(timezone.utc) - datetime.fromisoformat(state['checked_at'])).total_seconds() < cooldown:
                results.append({'source': source, 'ok': not bool(state['error']), 'message': '已在冷卻期間內檢查，沿用狀態', **dict(state)})
                continue
            checked = now()
            try:
                policy = RobotFileParser()
                policy.parse(fetch(f'https://{urlsplit(url).netloc}/robots.txt').decode('utf-8-sig').splitlines())
                if not policy.can_fetch(AGENT, url):
                    raise PermissionError('來源 robots 禁止此路徑，停止下載')
                records = parse(source, fetch(url))
                observed = now()
                inserted = 0
                with app.connect() as c:
                    for record in records:
                        fingerprint = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                        keys = list(record) + ['first_seen_at', 'retrieved_at', 'fingerprint']
                        values = list(record.values()) + [observed, observed, fingerprint]
                        inserted += c.execute(f"INSERT OR IGNORE INTO fenghsin_facts({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", values).rowcount
                    latest = max(r['announcement_date'] for r in records)
                    c.execute('''INSERT INTO fenghsin_source_status VALUES(?,?,?,NULL,?,?,?)
                        ON CONFLICT(source) DO UPDATE SET checked_at=excluded.checked_at,
                        success_at=excluded.success_at,error=NULL,observed_count=excluded.observed_count,
                        inserted_count=excluded.inserted_count,latest_date=excluded.latest_date''',
                              (source, checked, observed, len(records), inserted, latest))
                results.append({'source': source, 'ok': True, 'message': f'{name}：讀到{len(records)}筆數值，新增{inserted}筆；報價日期{latest}。下載成功不代表價格有變動。'})
            except Exception as exc:
                error = f'{type(exc).__name__}：{exc}'[:800]
                with app.connect() as c:
                    c.execute('''INSERT INTO fenghsin_source_status(source,checked_at,error) VALUES(?,?,?)
                        ON CONFLICT(source) DO UPDATE SET checked_at=excluded.checked_at,error=excluded.error''', (source, checked, error))
                results.append({'source': source, 'ok': False, 'message': f'{name}：{error}；保留既有歷史。'})
        return {'ok': all(r['ok'] for r in results), 'sources': results}
    finally:
        LOCK.release()


def reconstruct(events, anchor):
    """Only verified same-scope anchors; explicit consecutive announcements required.

    No generated calendar dates, no silent flat weeks, no inferred publishing time.
    Backfills returned separately with full parent IDs/equation, never direct quotes.
    """
    if anchor.get('verification') != 'cross_validated' or anchor.get('price') is None:
        return [], '缺少已獨立驗證的絕對價格基準；不進行歷史回推'
    scope = ('product', 'grade', 'tax_basis', 'price_basis')
    records = sorted(events, key=lambda r: r['announcement_date'], reverse=True)
    if len({r['announcement_date'] for r in records}) != len(records):
        return [], '同日公告存在多版本，停止回推並等待核對'
    current, output = anchor, []
    for previous in records:
        if previous['announcement_date'] >= current['announcement_date']:
            continue
        days = (date.fromisoformat(current['announcement_date']) - date.fromisoformat(previous['announcement_date'])).days
        if days > 7:
            return output, '公告相隔超過7日，停止回推；未假設缺週平盤'
        if any(previous.get(k) != current.get(k) for k in scope):
            return output, '品級、稅別或價格口徑不同，停止回推'
        if current.get('adjustment') is None:
            return output, '缺少本期明確調價，停止回推'
        price = current['price'] - current['adjustment']
        if price <= 0 or (previous.get('price') is not None and previous['price'] != price):
            return output, '來源價格矛盾或回推價格不合理，停止回推'
        derived = {**previous, 'price': price, 'kind': 'backfilled', 'verification': 'unverified',
                   'equation': f"{current['price']} − ({current['adjustment']}) = {price}",
                   'parent_ids': [current.get('id'), previous.get('id')], 'anchor_id': anchor.get('id')}
        output.append(derived)
        current = derived
    return output, '只回推至現有連續公告；所有回推值均與直接報價分開'


def facts():
    import app
    with app.connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM fenghsin_facts ORDER BY announcement_date,id')]


def view(product):
    records = [r for r in facts() if r['product'] == product]
    grouped = {}
    for r in records:
        grouped.setdefault(r['announcement_date'], []).append(r)
    points, conflicts = [], []
    for day, rows in sorted(grouped.items()):
        direct = [r for r in rows if r['price'] is not None]
        # Never arbitrarily overwrite a publisher correction or conflicting value.
        if len({r['price'] for r in direct}) > 1:
            conflicts.append({'date': day, 'reason': '同日絕對價格存在不同版本，暫不繪入主走勢', 'records': rows})
            continue
        if direct:
            row = direct[0]
            changes = [r for r in rows if r['adjustment'] is not None]
            points.append({**row, 'date': day, 'changes': changes, 'sources': direct})
    # Independently reported change vs observed absolute change: only <=7 days,
    # and no explicit intervening change event. This verifies change, NOT level.
    checks = []
    change_days = {r['announcement_date'] for r in records if r['adjustment'] is not None}
    for prev, cur in zip(points, points[1:]):
        days = (date.fromisoformat(cur['date']) - date.fromisoformat(prev['date'])).days
        if days <= 7 and not any(prev['date'] < d < cur['date'] for d in change_days):
            for change in cur['changes']:
                consistent = cur['price'] - prev['price'] == change['adjustment']
                checks.append({'date': cur['date'], 'consistent': consistent,
                               'reason': '絕對價差與另一來源調價一致；不代表絕對價已驗證' if consistent else '直接價格變化與調價公告矛盾',
                               'parent_ids': [prev['id'], cur['id'], change['id']]})
    return points, records, conflicts, checks


@router.get('/overview')
def overview():
    import app
    items = []
    for product, label in PRODUCTS.items():
        points, records, conflicts, checks = view(product)
        latest = points[-1] if points else None
        prev = points[-2] if len(points) >= 2 else None
        items.append({'product': product, 'name': label, 'latest': latest,
                      'previous': prev, 'count': len(points), 'earliest': points[0]['date'] if points else None,
                      'stale': bool(latest and (datetime.now(timezone(timedelta(hours=8))).date() - date.fromisoformat(latest['date'])).days > 8),
                      'conflicts': len(conflicts), 'consistent_changes': sum(c['consistent'] for c in checks)})
    with app.connect() as c:
        statuses = {r['source']: dict(r) for r in c.execute('SELECT * FROM fenghsin_source_status')}
    return {'items': items, 'sources': [{'id': k, 'name': n, 'url': u, 'status': statuses.get(k)} for k, (n, u) in SOURCES.items()],
            'notice': FACT_USE, 'backfill_status': '目前絕對價為單一彙整來源，尚未獨立驗證；自動回推停用，直接報價仍可查看。',
            'frequency': '週盤，包含臨時調價；只畫實際公告日期，不補成每日價格'}


@router.get('/history')
def history(product: str = 'scrap', months: int = Query(0, ge=0, le=120)):
    if product not in PRODUCTS:
        raise HTTPException(422, '不支援的豐興品項')
    points, records, conflicts, checks = view(product)
    cutoff = (datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=months * 30.4375)).isoformat() if months else '0001-01-01'
    points = [p for p in points if p['date'] >= cutoff]
    records = [r for r in records if r['announcement_date'] >= cutoff]
    conflicts = [r for r in conflicts if r['date'] >= cutoff]
    checks = [r for r in checks if r['date'] >= cutoff]
    prices = [p['price'] for p in points]
    change = prices[-1] - prices[0] if len(prices) >= 2 else None
    return {'product': product, 'name': PRODUCTS[product], 'points': points, 'records': records,
            'conflicts': conflicts, 'checks': checks,
            'stats': {'count': len(points), 'high': max(prices) if prices else None, 'low': min(prices) if prices else None,
                      'average': sum(prices) / len(prices) if prices else None, 'change': change,
                      'percent': change / prices[0] * 100 if change is not None else None},
            'reason': '至少需要兩個不同日期的有效絕對價格。可按更新取得公開歷史；調價／平盤不能取代絕對價。' if len(points) < 2 else '直接報價來自供應商公開表，未獨立確認品級與稅別；缺漏不補值。每筆均可追溯原始連結。',
            'backfills': [], 'backfill_reason': '缺少獨立驗證之絕對價基準，未執行回推。'}


@router.post('/sync')
def update():
    return sync(True)


def validate(c):
    for row in c.execute('SELECT * FROM fenghsin_facts'):
        if row['product'] not in PRODUCTS or row['source'] not in SOURCES:
            raise ValueError('豐興備份品項／來源無效')
        date.fromisoformat(row['announcement_date'])
        if row['price'] is not None and not 100 <= row['price'] <= 200000:
            raise ValueError('豐興備份價格無效')
        if urlsplit(row['source_url']).netloc != urlsplit(SOURCES[row['source']][1]).netloc:
            raise ValueError('豐興備份來源連結無效')
