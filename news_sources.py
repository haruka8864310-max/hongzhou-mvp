"""Public, factual bulletin metadata only. No article bodies or member content."""
import hashlib,json,re,threading
from datetime import date,datetime,timedelta,timezone
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit
from fastapi import APIRouter,HTTPException
import market,global_market as gm
from public_fetch import fetch as public_fetch,check_robots

router=APIRouter(prefix='/api/news')
LOCK=threading.Lock()
HOME='https://www.tymetals.com.tw/news/'
STEELNET='https://www.steelnet.com.tw/'
CATEGORIES=['鋼廠調漲／調降','廢鋼進出口','國際需求','鋼材價格','原料成本','產能與停工','關稅與貿易政策']


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS market_news(
        id INTEGER PRIMARY KEY,title TEXT NOT NULL,news_date TEXT NOT NULL,source TEXT NOT NULL,
        source_url TEXT NOT NULL,countries TEXT NOT NULL,commodities TEXT NOT NULL,
        categories TEXT NOT NULL,impact TEXT NOT NULL,reason TEXT NOT NULL,
        published_at TEXT,first_seen_at TEXT NOT NULL,retrieved_at TEXT NOT NULL,
        fingerprint TEXT UNIQUE NOT NULL,CHECK(impact IN ('up','down','mixed','unknown')))''')
    c.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(7)')


def classify(title):
    countries=[label for label,words in [('台灣',('台灣','臺灣','豐興','中鋼')),('日本',('日本','東京製鐵')),('美國',('美國','美国')),('中國',('中國','中国','大陸')),('土耳其',('土耳其',))] if any(w in title for w in words)]
    products=[label for label,words in [('廢鋼',('廢鋼','废钢')),('鋼筋',('鋼筋','螺紋鋼','螺纹钢')),('型鋼',('型鋼',)),('鐵礦砂',('鐵礦','铁矿')),('焦煤',('焦煤','焦炭')),('熱軋鋼',('熱軋','热轧'))] if any(w in title for w in words)]
    categories=[]
    rules=[('鋼廠調漲／調降',('調漲','調降','上調','下調')),('廢鋼進出口',('進口','出口','进口')),('國際需求',('需求','訂單','订单')),('鋼材價格',('盤價','開盤','鋼價','价格')),('原料成本',('鐵礦','铁矿','焦煤','成本')),('產能與停工',('停工','減產','减产','產能','产能')),('關稅與貿易政策',('關稅','关税','反傾銷','反倾销','貿易','贸易'))]
    for category,words in rules:
        if any(w in title for w in words):categories.append(category)
    impact='unknown';reason='標題未提供可核對的豐興廢鋼調價方向；影響不明，請查原文，不作情緒機率。'
    if '廢鋼' in products:
        up=any(w in title for w in ('調漲','上調','上漲'))
        down=any(w in title for w in ('調降','下調','下跌'))
        if up!=down:
            impact='up' if up else 'down'
            reason='標題明示廢鋼價格'+('上調' if up else '下調')+'，可列為報價壓力線索；未核對品級與傳導，不能當作豐興下週機率。'
        elif up and down:impact='mixed';reason='標題同時出現漲跌，市場／品級可能不同，不能合併方向。'
    return {'countries':countries or ['未明'],'commodities':products or ['鋼材（品項未載明）'],
            'categories':categories or ['鋼材價格'],'impact':impact,'reason':reason}


class Bulletins(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.item=None;self.date_depth=None;self.in_title=False;self.rows=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='div':
            self.depth+=1
            if 'newsItem' in a.get('class','').split():self.item={'depth':self.depth,'date':'','title':'','url':''}
            if self.item is not None and 'date' in a.get('class','').split():self.date_depth=self.depth
        if self.item is not None:
            if tag=='h3' and 'title' in a.get('class','').split():self.in_title=True
            if tag=='a' and self.in_title:self.item['url']=urljoin(HOME,a.get('href',''))
    def handle_data(self,data):
        if self.item is not None:
            if self.date_depth is not None:self.item['date']+=data
            if self.in_title:self.item['title']+=data
    def handle_endtag(self,tag):
        if tag=='h3':self.in_title=False
        if tag=='div':
            if self.date_depth==self.depth:self.date_depth=None
            if self.item is not None and self.item['depth']==self.depth:
                self.rows.append(self.item);self.item=None
            self.depth-=1


def parse(raw):
    parser=Bulletins();parser.feed(raw.decode('utf-8-sig'))
    rows=[]
    for item in parser.rows[:20]:
        title=re.sub(r'\s+','',item['title']);day=item['date'].strip();url=item['url']
        # Only ordinary factual company announcement names, no creative news titles.
        if not re.fullmatch(r'(?:豐興鋼鐵\d{2,4}\.\d{1,2}\.\d{1,2}開盤資訊|豐興鋼鐵\d{1,4}輪盤價資訊|中鋼\d{2,4}年\d{1,2}月份月盤盤價資訊|中鋼\d{2,4}年第[1-4]季季盤盤價資訊)',title):continue
        if not re.fullmatch(r'https://www\.tymetals\.com\.tw/news-detail/\d+/',url):raise ValueError('公告連結改變，未猜測文章位置')
        if date.fromisoformat(day)>datetime.now(timezone(timedelta(hours=8))).date():raise ValueError('未來公告日期')
        rows.append({'title':title,'news_date':day,'source':'同洋鋼鐵公開公告索引','source_url':url,**classify(title)})
    if not rows:raise ValueError('沒有可合法限縮擷取的事實式公告標題／日期；未讀文章全文')
    return rows


def fetch(url,audit):
    if url not in (HOME,'https://www.tymetals.com.tw/robots.txt'):raise ValueError('僅允許公開公告索引，未存取文章或會員內容')
    return public_fetch(url,{'www.tymetals.com.tw'},audit,1024*1024)


def state():
    with market.connection() as c:row=c.execute("SELECT value FROM market_settings WHERE name='news_status'").fetchone()
    return json.loads(row[0]) if row else {}


def sync(force=False):
    if not LOCK.acquire(False):raise HTTPException(409,'新聞公告更新中')
    before=state();checked=gm.now();result={**before,'checked_at':checked,'error':None,'attempts':[]}
    save=False
    try:
        if before.get('checked_at') and (datetime.now(timezone.utc)-datetime.fromisoformat(before['checked_at'])).total_seconds()<(60 if force else 21600):
            return {'ok':not bool(before.get('error')),'message':'未到新聞檢查時間，保留既有標題','status':before,'skipped':True}
        save=True;download=lambda url:fetch(url,result['attempts'])
        _,notice=check_robots(HOME,download);result['robots']=notice
        rows=parse(download(HOME));added=0;observed=gm.now()
        with market.connection() as c:
            for row in rows:
                fingerprint=hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                data={**row,'countries':json.dumps(row['countries'],ensure_ascii=False),'commodities':json.dumps(row['commodities'],ensure_ascii=False),'categories':json.dumps(row['categories'],ensure_ascii=False),'published_at':None,'first_seen_at':observed,'retrieved_at':observed,'fingerprint':fingerprint}
                keys=list(data)
                added+=c.execute(f"INSERT OR IGNORE INTO market_news({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",list(data.values())).rowcount
        result.update(success_at=observed,parsed=len(rows),added=added,latest_date=max(r['news_date'] for r in rows),
            usage='只保存通用事實式公告名稱、日期、連結及自建分類；不保存索引摘要、新聞全文或網站版面。非整站授權。',
            steelnet={'url':STEELNET,'automatic':False,'reason':'公開首頁HTTP200但未找到授權RSS；會員新聞及創作性標題再利用條件未確認，未繞過會員或保存內容。'})
    except Exception as exc:result['error']=f'{type(exc).__name__}：{exc}'[:700]
    finally:
        try:
            if save:
                with market.connection() as c:c.execute("INSERT INTO market_settings(name,value) VALUES('news_status',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(result,ensure_ascii=False),))
        finally:LOCK.release()
    return {'ok':not bool(result['error']),'message':result['error'] or f'新聞公告：讀到{result["parsed"]}筆，新增{result["added"]}筆；未儲存文章全文。','status':result}


@router.get('')
def listing():
    with market.connection() as c:rows=[dict(r) for r in c.execute('SELECT * FROM market_news ORDER BY news_date DESC,id DESC LIMIT 40')]
    seen=set();items=[]
    for r in rows:
        if r['source_url'] in seen:continue
        seen.add(r['source_url'])
        for field in ('countries','commodities','categories'):r[field]=json.loads(r[field])
        items.append(r)
    return {'items':items,'status':state(),'categories':CATEGORIES}


@router.post('/update')
def update():return sync(True)


def validate(c):
    for r in c.execute('SELECT * FROM market_news'):
        if not re.fullmatch(r'https://www\.tymetals\.com\.tw/news-detail/\d+/',r['source_url']):raise ValueError('新聞備份連結無效')
        date.fromisoformat(r['news_date'])
        if r['news_date']>datetime.now(timezone(timedelta(hours=8))).date().isoformat():raise ValueError('新聞備份包含未來日期')
        if not 1<=len(r['title'])<=200 or r['impact'] not in ('up','down','mixed','unknown'):raise ValueError('新聞備份內容無效')
        if not set(json.loads(r['categories']))<=set(CATEGORIES):raise ValueError('新聞分類無效')
