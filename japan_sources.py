"""Bounded extraction of Tokyo Steel's own published numeric purchase-price facts.

No news text, PDF layout or third-party JISRI ranges are stored/re-published.
robots restrictions and HTTP denials are respected; no alternative hosts/proxies.
"""
import hashlib,io,json,re,ssl,threading,unicodedata
from datetime import datetime,timezone,timedelta,date
from html.parser import HTMLParser
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urljoin,urlsplit
from urllib.robotparser import RobotFileParser
import certifi
from fastapi import APIRouter,HTTPException,Query
import market

router=APIRouter(prefix='/api/japan')
LOCK=threading.Lock()
HOME='https://www.tokyosteel.co.jp/scrapprice/'
ROBOTS='https://www.tokyosteel.co.jp/robots.txt'
PREFIX='https://www.tokyosteel.co.jp/assets/docs/scrapprice/price/'
AGENT='HongzhouMarketResearch/1.0'
FACT_USE='東京製鐵自行公開之數值事實，僅本機研究；不複製版面／全文、不再散布。非全面資料授權；依日本文化庁「單純事實／資料」指引。'
PLANTS=['田原工場','名古屋','岡山工場','関西','高松','九州工場','宇都宮工場','東京湾岸']
DELIVERY=['海上／陸上同欄','陸上','海上／陸上同欄','陸上','陸上','海上／陸上同欄','陸上','陸上']
GRADES=['電特A','特A','特級','一級','二級','級外','シュレッダーA','シュレッダーC']


def fetch(url):
    if url not in (HOME,ROBOTS) and not re.fullmatch(re.escape(PREFIX)+r'\d{4}/\d{4}\.\d{2}\.\d{2}\.pdf',url):raise ValueError('非核准官方下載網址')
    context=ssl.create_default_context();context.load_verify_locations(certifi.where())
    with urlopen(Request(url,headers={'User-Agent':AGENT}),context=context,timeout=12) as response:
        if response.status!=200:raise ValueError('官方回應不是完整HTTP200')
        if urlsplit(response.url).hostname!='www.tokyosteel.co.jp':raise ValueError('拒絕轉到其他來源')
        raw=response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:raise ValueError('官方檔案超過4MB')
    return raw


def robots():
    try:
        raw=fetch(ROBOTS).decode('utf-8-sig')
    except HTTPError as exc:
        if exc.code==404:return None,'robots.txt HTTP404，沒有宣告規則；不代表取得著作授權'
        raise
    policy=RobotFileParser();policy.parse(raw.splitlines())
    if not policy.can_fetch(AGENT,HOME):raise PermissionError('官方robots不允許索引存取，未下載價格')
    return policy,'已核對robots；仍僅處理公開數值事實'


class Links(HTMLParser):
    def __init__(self):super().__init__();self.urls=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            url=urljoin(HOME,dict(attrs).get('href',''))
            if re.fullmatch(re.escape(PREFIX)+r'\d{4}/\d{4}\.\d{2}\.\d{2}\.pdf',url):self.urls.append(url)


def links(raw):
    parser=Links();parser.feed(raw.decode('utf-8-sig'))
    valid=[]
    for url in set(parser.urls):
        period=url.rsplit('/',1)[-1][:-4]
        day=datetime.strptime(period,'%Y.%m.%d').date()
        if url.split('/')[-2]!=str(day.year):raise ValueError('官方路徑與報價年不符')
        if day<=datetime.now(timezone(timedelta(hours=8))).date():valid.append((day.isoformat(),url))
    if not valid:raise ValueError('官方頁沒有可核對的價格PDF，未猜測端點')
    return sorted(valid,reverse=True)


def normal(text):return re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).replace('-','ー').replace('−','ー')


def parse_layout(text,expected):
    """Use validated fixed eight-column layout; do not shift cells across blank grades."""
    lines=text.splitlines();compact=normal(text)
    if '国内鉄スクラップ購入価格表' not in compact:raise ValueError('不是官方國內購入價格表')
    dates=re.findall(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',text)
    if len(dates)<2:raise ValueError('缺公告日或適用日')
    issued,effective=[date(*map(int,d)).isoformat() for d in dates[:2]]
    if effective!=expected or issued>effective:raise ValueError('PDF適用日、公告日或檔名不一致')
    header=next((line for line in lines if all(p in normal(line) for p in PLANTS)),None)
    if not header or [normal(header).index(p) for p in PLANTS]!=sorted(normal(header).index(p) for p in PLANTS):raise ValueError('工廠欄位改變，拒絕猜測對應')
    reference=None;centres=None;result={}
    for line in lines:
        prices=list(re.finditer(r'(?<!\d)\d{1,3},\d{3}(?!\d)',line))
        if not prices:continue
        label=normal(line[:prices[0].start()])
        if label not in GRADES:continue
        if label in result:raise ValueError('同一品級重複出現')
        if len(prices)!=8:raise ValueError('重要品級不是8個工廠完整欄位，未猜測空白價格')
        ends=[p.end() for p in prices]
        if reference is None:reference=ends;centres=[(p.start()+p.end())/2 for p in prices]
        if any(abs(a-b)>2 for a,b in zip(reference,ends)):raise ValueError('價格欄位位移，未儲存')
        values=[int(p[0].replace(',','')) for p in prices]
        if any(not 1000<=v<=1000000 for v in values):raise ValueError('價格不符合日圓／公噸範圍')
        result[label]=values
    if set(result)!=set(GRADES):raise ValueError('官方重要品級欄位不完整')
    shipping=[set() for _ in PLANTS]
    for line in lines:
        for token in re.finditer(r'[海陸]\s*上',line):
            position=(token.start()+token.end())/2
            column=min(range(8),key=lambda i:abs(centres[i]-position))
            if abs(centres[column]-position)>4.5:raise ValueError('到貨方式欄位位移')
            shipping[column].add(normal(token[0]))
    if shipping!=[{'海上','陸上'} if i in (0,2,5) else {'陸上'} for i in range(8)]:raise ValueError('海上／陸上到貨欄位改變，拒絕混合報價條件')
    return {'issued_date':issued,'quote_date':effective,'prices':[(plant,grade,DELIVERY[i],result[grade][i]) for grade in GRADES for i,plant in enumerate(PLANTS)]}


def parse_pdf(raw,expected):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw),strict=True)
    if reader.is_encrypted or not 1<=len(reader.pages)<=4:raise ValueError('PDF頁數或加密狀態不符')
    # Only the absolute-price page, never the second-page adjustment table.
    text=reader.pages[0].extract_text(extraction_mode='layout')
    try:return parse_layout(text,expected)
    except ValueError:
        return parse_legacy_layout(text,expected)


def parse_legacy_layout(text,expected):
    """Explicit 2025 7-site / 8-site-with-suspended-site layouts.

    Map price coordinates to the printed header, never shift blank cells left.
    A blank site is accepted ONLY if the document explicitly says suspended.
    """
    if '国内鉄スクラップ購入価格表' not in normal(text):raise ValueError('不是官方購入表')
    dates=re.findall(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',text)
    if len(dates)<2:raise ValueError('缺少公告日／適用日')
    issued,effective=[date(*map(int,d)).isoformat() for d in dates[:2]]
    if effective!=expected or issued>effective:raise ValueError('公告／適用日期不符')
    lines=text.splitlines()
    header=next((line for line in lines if all(p in normal(line) for p in PLANTS[:7])),None)
    if not header:raise ValueError('歷史工廠表頭不完整')
    plants=PLANTS if '東京湾岸' in normal(header) else PLANTS[:7]
    positions=[]
    for plant in plants:
        match=re.search(r'\s*'.join(map(re.escape,plant)),header)
        if not match:raise ValueError('歷史表頭工廠無法定位')
        positions.append((match.start()+match.end())/2)
    if positions!=sorted(positions) or any(b-a<7 for a,b in zip(positions,positions[1:])):raise ValueError('歷史表頭欄位順序或間距不符')
    def column(start,end):
        center=(start+end)/2
        i=min(range(len(plants)),key=lambda j:abs(positions[j]-center))
        if abs(positions[i]-center)>5:raise ValueError('歷史欄位無法可靠對齊')
        return i
    suspended=set()
    for line in lines:
        for match in re.finditer('休止中',line):suspended.add(column(match.start(),match.end()))
    active=set(range(len(plants)))-suspended
    result={}
    shipping=[set() for _ in plants]
    for line in lines:
        for match in re.finditer(r'[海陸]\s*上',line):shipping[column(match.start(),match.end())].add(normal(match[0]))
        prices=list(re.finditer(r'(?<!\d)\d{1,3},\d{3}(?!\d)',line))
        if not prices:continue
        grade=normal(line[:prices[0].start()])
        if grade not in GRADES:continue
        if grade in result:raise ValueError('歷史同品級重複')
        values={}
        for match in prices:
            index=column(match.start(),match.end())
            if index in values:raise ValueError('歷史價格同欄重複')
            value=int(match[0].replace(',',''))
            if not 1000<=value<=1000000:raise ValueError('歷史價格範圍無效')
            values[index]=value
        if set(values)!=active:raise ValueError('歷史重要品級缺值但未明示休止，拒絕補值／移欄')
        result[grade]=values
    if set(result)!=set(GRADES):raise ValueError('歷史重要品級不完整')
    if shipping!=[{'海上','陸上'} if i in (0,2,5) else {'陸上'} for i in range(len(plants))]:raise ValueError('歷史海陸欄位不符')
    return {'issued_date':issued,'quote_date':effective,
            'prices':[(plants[i],grade,DELIVERY[i],result[grade][i]) for grade in GRADES for i in sorted(active)]}


def state():
    with market.connection() as c:r=c.execute("SELECT value FROM market_settings WHERE name='japan_tokyo_status'").fetchone()
    return json.loads(r[0]) if r else {}


def save_state(value):
    with market.connection() as c:c.execute("INSERT INTO market_settings(name,value) VALUES('japan_tokyo_status',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(value,ensure_ascii=False),))


def sync(force=False,history_year=None):
    if not LOCK.acquire(False):raise HTTPException(409,'日本官方來源正在更新')
    import global_market as gm
    before=state();stamp=gm.now();result={**before,'checked_at':stamp,'error':None,'new_versions':0,'parsed_observations':0,'files':[],'failures':[]}
    try:
        wait=60 if force else 86400
        if before.get('checked_at') and (datetime.now(timezone.utc)-datetime.fromisoformat(before['checked_at'])).total_seconds()<wait:
            return {'ok':not bool(before.get('error')),'message':'未到檢查時間，沿用本機日本資料；上次原因：'+str(before.get('error') or '無'),'skipped':True}
        policy,notice=robots();listed=links(fetch(HOME));result['robots']=notice
        with market.connection() as c:origins=[r[0] for r in c.execute("SELECT origin FROM global_quotes WHERE market='jp_tokyo' AND origin LIKE 'tokyo|%'")]
        done={s.split('|')[1] for s in origins}
        # Latest PDF is checked for revisions; at most 12 public tables per daily run.
        selected=listed[:1]+[(d,u) for d,u in listed[1:] if u not in done][:11]
        if history_year is not None:
            selected=sorted((d,u) for d,u in listed if d.startswith(str(history_year)+'-') and u not in done)[:12]
            if not selected:return {'ok':True,'message':f'官方索引中{history_year}年已無尚未保存的價格表；未猜測其他網址。','skipped':True}
        added=0;parsed_count=0;errors=[];files=[]
        for day,url in selected:
            try:
                if policy and not policy.can_fetch(AGENT,url):raise PermissionError('robots禁止此PDF，不下載')
                parsed=parse_pdf(fetch(url),day);digest=hashlib.sha256(json.dumps(parsed,ensure_ascii=False).encode()).hexdigest()[:16]
                retrieved=gm.now();file_added=0
                with market.connection() as c:
                    for plant,grade,term,price in parsed['prices']:
                        value=gm.Observation(market='jp_tokyo',grade=plant+'／'+grade,delivery_term='東京製鐵工廠購入；'+term+'；非FOB，付款依原公告',currency='JPY',unit='t',price=price,quote_date=day,published_at=None,frequency='observed',source='東京製鐵官方購入價格表',source_url=HOME,license_note=FACT_USE+' 公告日'+parsed['issued_date']+'（發布時間未知）；品級不擅自改稱H2。')
                        file_added+=gm.insert(c,value,retrieved,'tokyo|'+url+'|'+plant+'|'+grade+'|'+digest,'automatic')
                added+=file_added
                parsed_count+=len(parsed['prices']);files.append({'url':url,'date':day,'observations':len(parsed['prices']),'http_status':200})
            except Exception as exc:errors.append({'url':url,'reason':type(exc).__name__+': '+str(exc)[:200]})
        if not files:raise ValueError('所有價格表解析失敗：'+json.dumps(errors,ensure_ascii=False)[:400])
        with market.connection() as c:summary=c.execute("SELECT COUNT(*),MIN(quote_date),MAX(quote_date),COUNT(DISTINCT series_id) FROM global_quotes WHERE market='jp_tokyo' AND data_status='automatic'").fetchone()
        with market.connection() as c:complete={r[0].split('|')[1] for r in c.execute("SELECT origin FROM global_quotes WHERE origin LIKE 'tokyo|%'")}
        result.update(success_at=gm.now(),latest_date=summary[2],first_date=summary[1],stored_versions=summary[0],series=summary[3],new_versions=added,parsed_observations=parsed_count,files=files,failures=errors,index_files=len(listed),remaining_files=sum(u not in complete for _,u in listed),archive_year=history_year,error='部分PDF未取得／格式不符；成功歷史保留' if errors else None)
        save_state(result)
        return {'ok':not errors,'message':f'日本官方表：解析{parsed_count}筆，追加{added}筆版本；已保存{summary[1]}至{summary[2]}，不是即時行情。','status':result}
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)[:450];save_state(result)
        return {'ok':False,'message':'日本來源失敗，既有歷史保留：'+result['error'],'status':result}
    finally:LOCK.release()


@router.get('/status')
def status():
    return {'tokyo':state(),'jisri':{'automatic':False,'stored':0,'url':'https://www.jisri.or.jp/market','reason':'H2地區區間註明出所為日刊市況通信社；第三方保存／再利用條件未確認。2023年4月起停止刊載東京／中部／關西三地區月均，不以其替代當前H2價。'}}


@router.post('/update')
def update():return sync(True)


@router.post('/history')
def history(year:int=Query(2025,ge=2025,le=datetime.now().year)):
    return sync(True,year)


def validate(c):
    row=c.execute("SELECT value FROM market_settings WHERE name='japan_tokyo_status'").fetchone()
    if not row:return
    data=json.loads(row[0])
    if not isinstance(data,dict):raise ValueError('日本來源狀態格式無效')
    for key in ('checked_at','success_at'):
        if data.get(key):
            stamp=datetime.fromisoformat(data[key])
            if stamp.tzinfo is None or stamp>datetime.now(timezone.utc):raise ValueError('日本來源時間無效')
    if not isinstance(data.get('files',[]),list) or len(data.get('files',[]))>12:raise ValueError('日本來源下載清單無效')
    for entry in data.get('files',[]):
        if not re.fullmatch(re.escape(PREFIX)+r'\d{4}/\d{4}\.\d{2}\.\d{2}\.pdf',entry['url']):raise ValueError('日本來源網址不符')
        date.fromisoformat(entry['date'])
        if entry['observations'] not in (48,56,64):raise ValueError('日本來源筆數不符')
