"""NBS official fixed-grade rebar ten-day statistics, never scrap/futures prices."""
import calendar,hashlib,json,re,threading,unicodedata
from datetime import date,datetime,timedelta,timezone
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit
from fastapi import APIRouter,HTTPException
import market,global_market as gm
from fenghsin import Tables
from public_fetch import fetch as public_fetch,check_robots,SourceError,AGENT

router=APIRouter(prefix='/api/china')
LOCK=threading.Lock()
HOME='https://www.stats.gov.cn/sj/zxfb/'
TERMS='https://www.stats.gov.cn/wzgl/202302/t20230217_1912857.html'
HOSTS={'www.stats.gov.cn'}
ATTRIBUTION='引自中國國家統計局網站（www.stats.gov.cn）；統計資料下載使用依其服務條款；僅數值，不複製全文或版面。'
TITLE=re.compile(r'(20\d{2})年(\d{1,2})月(上旬|中旬|下旬)流通领域重要生产资料市场价格变动情况')


def fetch(url,audit):
    allowed=url in (HOME,TERMS,'https://www.stats.gov.cn/robots.txt') or re.fullmatch(r'https://www\.stats\.gov\.cn/sj/(?:zxfb|zxfbhjd)/\d{6}/t\d{8}_\d+\.html',url)
    if not allowed:raise SourceError(url,'非核准統計下載路徑')
    return public_fetch(url,HOSTS,audit)


class Index(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.links={}
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.current=[dict(attrs).get('href',''),'']
    def handle_data(self,data):
        if self.current is not None:self.current[1]+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.current is not None:
            url,title=self.current;match=TITLE.fullmatch(re.sub(r'\s+','',title))
            if match:
                url=urljoin(HOME,url)
                if urlsplit(url).hostname in HOSTS:self.links[url]=match.group(0)
            self.current=None


class Meta(HTMLParser):
    def __init__(self):super().__init__();self.values={}
    def handle_starttag(self,tag,attrs):
        if tag=='meta':
            a=dict(attrs)
            if a.get('name') in ('ArticleTitle','PubDate','ContentSource'):self.values[a['name']]=a.get('content','')


def parse(raw,url):
    html=raw.decode('utf-8-sig');meta=Meta();meta.feed(html)
    # Older official templates expose original title/time only in the visible
    # header, not meta tags. Accept a unique exact title and header timestamp;
    # never use migration URL dates or infer a historical publication clock.
    if not meta.values.get('ArticleTitle'):
        from html import unescape
        titles={re.sub(r'\s+','',unescape(re.sub('<[^>]+>','',x))) for x in re.findall(r'<h1[^>]*>(.*?)</h1>',html,re.S)}
        titles={x for x in titles if TITLE.fullmatch(x)}
        if len(titles)==1:meta.values['ArticleTitle']=titles.pop()
    if not meta.values.get('PubDate'):
        stamps=set(re.findall(r'''class=["']detail-title-des["'][\s\S]{0,500}?<p>\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s*</p>''',html))
        if len(stamps)==1:meta.values['PubDate']=stamps.pop()
    match=TITLE.fullmatch(re.sub(r'\s+','',meta.values.get('ArticleTitle','')))
    if not match:raise ValueError('不是指定生產資料旬度統計公告')
    y,m=int(match[1]),int(match[2]);part=match[3]
    first,last={'上旬':(1,10),'中旬':(11,20),'下旬':(21,calendar.monthrange(y,m)[1])}[part]
    start,end=date(y,m,first),date(y,m,last)
    try:published=datetime.strptime(meta.values['PubDate'],'%Y/%m/%d %H:%M').replace(tzinfo=timezone(timedelta(hours=8)))
    except (KeyError,ValueError) as exc:raise ValueError('缺少可信發布時間，不以檔名日期代替') from exc
    if published.date()<end or published>datetime.now(timezone.utc) or end>datetime.now(timezone(timedelta(hours=8))).date():raise ValueError('發布時間／統計期間無效或未來資料')
    parser=Tables();parser.feed(html);values=set()
    for table in parser.tables:
        for row in table:
            cells=[re.sub(r'\s+','',unicodedata.normalize('NFKC',c['text'])) for c in row]
            if cells and cells[0]=='螺纹钢(Φ20mm,HRB400E)':
                if len(cells)!=5 or cells[1]!='吨':raise ValueError('螺紋鋼欄位／單位改變')
                if not all(re.fullmatch(r'[+-]?\d+(?:\.\d+)?',v) for v in cells[2:]):raise ValueError('螺紋鋼價格／調價不是數值')
                values.add(tuple(float(v) for v in cells[2:]))
    # Desktop/mobile duplicates are permitted only when every number agrees.
    if len(values)!=1:raise ValueError('找不到唯一一致的HRB400E Φ20mm價格，未混合規格')
    price,change,percent=values.pop()
    if not 100<=price<=100000 or price-change<=0:raise ValueError('價格範圍或前期基準無效')
    if abs(change/(price-change)*100-percent)>.11:raise ValueError('公告變動與百分比矛盾')
    return {'quote_date':end.isoformat(),'period_start':start.isoformat(),'price':price,'adjustment':change,
            'percent':percent,'published_at':published.astimezone(timezone.utc).isoformat(),
            'source_url':url,'title':meta.values['ArticleTitle']}


def state():
    with market.connection() as c:row=c.execute("SELECT value FROM market_settings WHERE name='china_nbs_status'").fetchone()
    return json.loads(row[0]) if row else {}


def save(value):
    with market.connection() as c:c.execute("INSERT INTO market_settings(name,value) VALUES('china_nbs_status',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(value,ensure_ascii=False),))


def sync(force=False):
    if not LOCK.acquire(False):raise HTTPException(409,'中國官方統計更新中')
    before=state();result={**before,'checked_at':gm.now(),'error':None,'attempts':[],'failures':[],'new_versions':0}
    should_save=False
    try:
        if before.get('checked_at') and (datetime.now(timezone.utc)-datetime.fromisoformat(before['checked_at'])).total_seconds()<(60 if force else 21600):
            return {'ok':not bool(before.get('error')),'message':'未到檢查時間，保留既有中國統計','skipped':True,'status':before}
        should_save=True
        download=lambda url:fetch(url,result['attempts'])
        policy,notice=check_robots(HOME,download);result['robots']=notice
        index=Index();index.feed(download(HOME).decode('utf-8-sig'))
        if not index.links:raise ValueError('官方索引未列出生產資料旬度公告，未猜測歷史網址')
        parsed=0
        for url in sorted(index.links,reverse=True)[:6]:
            try:
                if policy and not policy.can_fetch(AGENT,url):raise SourceError(url,'robots禁止公告下載')
                data=parse(download(url),url);stamp=gm.now()
                value=dict(market='cn_rebar',grade='HRB400E Φ20mm',delivery_term='中國流通領域批發／銷售旬均統計；稅運費依官方口徑；非期貨、非廢鋼',currency='CNY',unit='t',price=data['price'],adjustment=data['adjustment'],quote_date=data['quote_date'],published_at=data['published_at'],frequency='observed',source='中國國家統計局',source_url=HOME,license_note=ATTRIBUTION+' 旬均期間'+data['period_start']+'～'+data['quote_date']+'；公告變動'+str(data['adjustment'])+'。')
                digest=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
                with market.connection() as c:result['new_versions']+=gm.insert(c,value,stamp,'nbs|'+url+'|'+digest,'automatic')
                parsed+=1
            except Exception as exc:result['failures'].append({'url':url,'http_status':getattr(exc,'status',200),'error':f'{type(exc).__name__}：{exc}'[:500]})
        if not parsed:raise ValueError('所有旬度公告失敗，原始數據保留')
        with market.connection() as c:
            count=c.execute("SELECT COUNT(*),COUNT(DISTINCT quote_date),MIN(quote_date),MAX(quote_date) FROM global_quotes WHERE origin LIKE 'nbs|%'").fetchone()
        result.update(success_at=gm.now(),parsed_releases=parsed,stored_versions=count[0],dates=count[1],first_date=count[2],latest_date=count[3],error='部分官方公告取得失敗，成功歷史保留' if result['failures'] else None)
    except Exception as exc:
        result['error']=f'{type(exc).__name__}：{exc}'[:700]
    finally:
        try:
            if should_save:save(result)
        finally:LOCK.release()
    return {'ok':not bool(result['error']),'message':result['error'] or f'中國螺紋鋼：解析{result["parsed_releases"]}期，新增{result["new_versions"]}筆版本；最新統計期{result["latest_date"]}，非即時價。','status':result}


@router.post('/update')
def update():return sync(True)


@router.get('/status')
def status():return state()
