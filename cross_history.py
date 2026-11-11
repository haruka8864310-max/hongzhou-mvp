"""Repeatable bounded official archive imports into the existing market ledger."""
import argparse,calendar,hashlib,json,re,time
from datetime import datetime,timezone
from html.parser import HTMLParser
import china_sources as cn,global_market as gm,market
from public_fetch import check_robots,AGENT,SourceError
from urllib.parse import urlsplit

_session=None
def fetch(url,hosts,audit,limit=3*1024*1024):
    """Reuse verified TLS connections, no retries, fingerprint tricks or cookies."""
    global _session
    if urlsplit(url).scheme!='https' or urlsplit(url).hostname not in hosts:raise SourceError(url,'來源不在白名單')
    import requests
    if _session is None:
        _session=requests.Session();_session.headers['User-Agent']=AGENT
    item={'url':url,'http_status':None};audit.append(item)
    try:
        _session.cookies.clear()
        with _session.get(url,timeout=(5,12),stream=True) as r:
            item.update(http_status=r.status_code,content_type=r.headers.get('Content-Type'))
            if r.status_code!=200:raise SourceError(url,'HTTP '+str(r.status_code),r.status_code)
            if urlsplit(r.url).hostname not in hosts:raise SourceError(url,'拒絕跨站轉址')
            data=b'';start=time.monotonic()
            for chunk in r.iter_content(65536):
                if time.monotonic()-start>30:raise SourceError(url,'下載超過30秒，保留舊資料',r.status_code)
                data+=chunk
                if len(data)>limit:raise SourceError(url,'檔案超過下載上限',200)
            return data
    except Exception as exc:
        item['error']=str(exc)
        if isinstance(exc,SourceError):raise
        raise SourceError(url,str(exc),item['http_status']) from exc

FED={'fx_usd':'https://www.federalreserve.gov/releases/h10/hist/dat00_ta.htm',
     'jpy_per_usd':'https://www.federalreserve.gov/releases/h10/hist/dat00_ja.htm'}


def state():
    with market.connection() as c:r=c.execute("SELECT value FROM market_settings WHERE name='cross_history'").fetchone()
    return json.loads(r[0]) if r else {}


def save(value):
    with market.connection() as c:c.execute("INSERT INTO market_settings VALUES('cross_history',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(value,ensure_ascii=False),))


def store_release(raw,url):
    data=cn.parse(raw,url);stamp=gm.now();added=0
    # Every original release gets its own content fingerprint and publication
    # timestamp. Never backdate first_seen/retrieved to its historical release.
    from fenghsin import Tables
    parser=Tables();parser.feed(raw.decode('utf-8-sig'));hc=set()
    for table in parser.tables:
        for row in table:
            cells=[cn.unicodedata.normalize('NFKC',c['text']) for c in row]
            cells=[re.sub(r'\s+','',s) for s in cells]
            if cells and re.fullmatch(r'热轧普通板卷\(4\.75[—–~-]11\.5mm,Q235\)(?:注2)?',cells[0]):
                if len(cells)!=5 or cells[1]!='吨':raise ValueError('熱軋規格欄位改變')
                hc.add(float(cells[2]))
    values=[('cn_rebar','HRB400E Φ20mm',data['price'])]
    if len(hc)==1:values.append(('cn_hrc','Q235 4.75—11.5mm',hc.pop()))
    for key,grade,price in values:
        v=dict(market=key,grade=grade,delivery_term='中國流通領域批發／銷售旬均統計；非期貨非廢鋼',currency='CNY',unit='t',
               price=price,quote_date=data['quote_date'],published_at=data['published_at'],frequency='observed',source='中國國家統計局',
               source_url=url,license_note=cn.ATTRIBUTION+' 原始旬度公告；只取統計數值。')
        digest=hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()[:20]
        with market.connection() as c:added+=gm.insert(c,v,stamp,'archive-nbs|'+url+'|'+key+'|'+digest,'automatic')
    return added,len(values)


def import_nbs(pages=67,max_files=160,refresh_indexes=()):
    if not 1<=pages<=67 or not 1<=max_files<=160:raise ValueError('下載上限超出允許範圍')
    result=state();audit=[];failures=[];added=0;parsed=0
    def download(u):return fetch(u,{'www.stats.gov.cn'},audit)
    policy,notice=check_robots(cn.HOME,download)
    home=download(cn.HOME).decode('utf-8-sig')
    count=re.search(r'createPageHTML\(\s*(\d+),\s*0,\s*"index",\s*"html"',home)
    if not count:raise ValueError('官方未宣告可核對的歷史分頁，未猜網址')
    cached=state().get('nbs',{}).get('index_urls',[])
    urls={u:'' for u in cached if re.fullmatch(r'https://www\.stats\.gov\.cn/sj/(?:zxfb|zxfbhjd)/\d{6}/t\d{8}_\d+\.html',u)}
    cached_pages=bool(urls and state().get('nbs',{}).get('index_pages',0)>=pages)
    indices=set([0] if cached_pages else range(min(pages,int(count[1]))))
    indices.update(state().get('nbs',{}).get('failed_indexes',[]))
    indices.update(refresh_indexes)
    if any(not isinstance(i,int) or not 0<=i<int(count[1]) for i in indices):raise ValueError('非官網宣告的索引頁')
    failed_indexes=[]
    for i in sorted(indices):
        url=cn.HOME+('index_'+str(i)+'.html' if i else '')
        if policy and not policy.can_fetch(AGENT,url):raise ValueError('robots不允許歷史索引')
        try:
            html=home if not i else download(url).decode('utf-8-sig')
            parser=cn.Index();parser.feed(html);urls.update(parser.links)
        except Exception as exc:
            failures.append({'url':url,'http_status':getattr(exc,'status',None),'error':str(exc)});failed_indexes.append(i)
        if i%10==0:print('NBS index',i,'links',len(urls),'failures',len(failures),flush=True)
        time.sleep(.15)
    with market.connection() as c:
        stored={}
        for r in c.execute("SELECT origin,market FROM global_quotes WHERE origin LIKE 'archive-nbs|%'"):
            stored.setdefault(r[0].split('|')[1],set()).add(r[1])
        done={url for url,markets in stored.items() if markets=={'cn_rebar','cn_hrc'}}
    # Feb-3 migration files contain pre-2023 mixed Φ16–25mm scopes. They need
    # a separate reviewed series, not repeated attempts to force Φ20mm parsing.
    migrations=[u for u in urls if '/202302/t20230203_' in u]
    selected=[u for u in sorted(urls,reverse=True) if u not in done and u not in migrations][:max_files]
    for i,url in enumerate(selected):
        try:
            if policy and not policy.can_fetch(AGENT,url):raise ValueError('robots不允許此公告')
            new,n=store_release(download(url),url);added+=new;parsed+=n
        except Exception as exc:
            failures.append({'url':url,'http_status':getattr(exc,'status',None),'error':str(exc)})
            print('NBS failed',url,str(exc)[:180],flush=True)
        if (i+1)%10==0:print('NBS releases checked',i+1,'/',len(selected),flush=True)
        time.sleep(.25)
    result=state()
    result.update(checked_at=gm.now(),nbs={'added':added,'parsed':parsed,'linked_releases':len(urls),'index_urls':list(urls),'index_pages':min(pages,int(count[1])),
                                        'failed_indexes':failed_indexes,'excluded_migration_files':len(migrations),'remaining':max(0,len(urls)-len(done)-len(selected)-len(migrations)),
                                        'failures':failures,'attempts':audit,'robots':notice})
    save(result);return result['nbs']


def import_fed():
    result=state();audit=[];added=0;missing={}
    from fenghsin import Tables
    for key,url in FED.items():
        raw=fetch(url,{'www.federalreserve.gov'},audit,limit=2*1024*1024)
        html=raw.decode('utf-8-sig');parser=Tables();parser.feed(html)
        # This aggregate is today's vintage, not an as-published historical file.
        rows=[];nulls=0
        for table in parser.tables:
            for row in table:
                cells=[c['text'].strip() for c in row]
                if len(cells)!=2:continue
                try:day=datetime.strptime(cells[0],'%d-%b-%y').date().isoformat()
                except ValueError:continue
                if cells[1]=='ND':nulls+=1;continue
                price=float(cells[1]);rows.append((day,price))
        if not rows:raise ValueError('Fed匯率欄位無法解析')
        with market.connection() as c:
          for day,price in rows:
            value=dict(market=key,grade='Fed紐約中午參考匯率',delivery_term='Fed H.10原始幣對；本次歷史快照，非歷史發布版本',currency='TWD' if key=='fx_usd' else 'JPY',
                       unit='TWD per USD' if key=='fx_usd' else 'JPY per USD',price=price,quote_date=day,published_at=None,frequency='daily',source='Federal Reserve Board H.10',
                       source_url=url,license_note='Fed Board公有領域資料；註明來源；各筆歷史公布時間未知，不回填發布時間。')
            digest=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
            added+=gm.insert(c,value,gm.now(),'fed-history|'+digest,'automatic')
        missing[key]={'valid':len(rows),'missing':nulls,'missing_ratio':nulls/(len(rows)+nulls),'first':rows[0][0],'last':rows[-1][0]}
    result=state();result['fed']={'added':added,'coverage':missing,'attempts':audit};save(result);return result['fed']


def import_bls():
    import requests,metal_engine as engine
    added=0;audit=[]
    for first,last in [(1997,2006),(2007,2016)]:
        payload={'seriesid':['WPU1012','WPU102301'],'startyear':str(first),'endyear':str(last)}
        entry={'url':engine.BLS_URL,'request':payload};audit.append(entry)
        try:
            r=requests.post(engine.BLS_URL,json=payload,headers={'User-Agent':AGENT},timeout=20)
            entry['http_status']=r.status_code;r.raise_for_status()
            parsed,_=engine.parse_bls(r.content)
            count,revisions=engine.store('bls',parsed,None);added+=count;entry.update(added=count,revisions=revisions)
        except Exception as exc:entry['error']=str(exc)
        time.sleep(1)
    value={'added':added,'attempts':audit};result=state();result['bls']=value;save(result);return value


def inventory():
    with market.connection() as c:
        groups=[dict(r) for r in c.execute("""SELECT market,grade,currency,unit,source,COUNT(*) versions,COUNT(DISTINCT quote_date) observations,
          MIN(quote_date) first_date,MAX(quote_date) last_date,SUM(published_at IS NULL)*1.0/COUNT(*) missing_publication_ratio
          FROM global_quotes WHERE origin LIKE 'archive-nbs|%' OR origin LIKE 'nbs|%' OR origin LIKE 'fed-history|%' GROUP BY market,grade,currency,unit,source""")]
        bls=c.execute("SELECT COUNT(DISTINCT period),MIN(period),MAX(period) FROM metal_observations WHERE series_id='bls_steel_scrap'").fetchone()
        if bls[0]:
            a,b=[tuple(map(int,v.split('-'))) for v in (bls[1],bls[2])]
            expected=(b[0]-a[0])*12+b[1]-a[1]+1
            groups.append({'market':'bls_steel_scrap','source':'BLS WPU1012（月度指數，非貨櫃廢鋼實價）','observations':bls[0],'first_date':bls[1],'last_date':bls[2],'missing_publication_ratio':1.0,'missing_price_ratio':1-bls[0]/expected})
        for key,source,query,args in [
            ('fh_scrap','豐興廢鋼（既有；本輪新增0）',"SELECT COUNT(DISTINCT announcement_date),MIN(announcement_date),MAX(announcement_date),AVG(published_at IS NULL) FROM fenghsin_facts WHERE product='scrap' AND price IS NOT NULL",()),
            ('jp_tokyo','東京製鐵岡山／二級（既有；本輪新增0）',"SELECT COUNT(DISTINCT quote_date),MIN(quote_date),MAX(quote_date),AVG(published_at IS NULL) FROM global_quotes WHERE market='jp_tokyo' AND grade=?",('岡山工場／二級',))]:
            row=c.execute(query,args).fetchone()
            if row[0]:groups.append({'market':key,'source':source,'observations':row[0],'first_date':row[1],'last_date':row[2],'missing_publication_ratio':row[3],'missing_price_ratio':None,'coverage_note':'完整歷史缺值率未知；不是所有週／日均有公告。'})
    for g in groups:
        if g['source']=='Federal Reserve Board H.10':g['missing_price_ratio']=state().get('fed',{}).get('coverage',{}).get(g['market'],{}).get('missing_ratio')
        if g['market'] in ('cn_rebar','cn_hrc'):
            start=datetime.fromisoformat(g['first_date']);end=datetime.fromisoformat(g['last_date']);expected=0
            y,m=start.year,start.month
            while (y,m)<=(end.year,end.month):
                for d in (10,20,calendar.monthrange(y,m)[1]):
                    if g['first_date']<=f'{y:04}-{m:02}-{d:02}'<=g['last_date']:expected+=1
                y,m=(y+1,1) if m==12 else (y,m+1)
            g.update(expected_periods=expected,missing_price_ratio=1-g['observations']/expected if expected else None)
    return {'imports':state(),'series':groups}


def apply_verified(path):
    """Append only verified public ledger rows from a separately tested import.
    No database file replacement, company-table copy, or synthetic fixture load.
    """
    import sqlite3,metal_engine as engine
    from pathlib import Path
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('匯入資料庫不存在')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as source:
        source.row_factory=sqlite3.Row
        if source.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('來源SQLite損壞')
        rows=source.execute("SELECT * FROM global_quotes WHERE origin LIKE 'archive-nbs|%' OR origin LIKE 'fed-history|%'").fetchall()
        added=0
        with market.connection() as dest:
            for r in rows:
                if r['origin'].startswith('archive-nbs|'):
                    if r['source']!='中國國家統計局' or not re.fullmatch(r'https://www\.stats\.gov\.cn/sj/(?:zxfb|zxfbhjd)/\d{6}/t\d{8}_\d+\.html',r['source_url']):raise ValueError('非官方歷史公告')
                elif r['source_url'] not in FED.values() or r['source']!='Federal Reserve Board H.10':raise ValueError('非Fed官方匯率')
                value={key:r[key] for key in gm.Observation.model_fields}
                added+=gm.insert(dest,value,r['retrieved_at'],r['origin'],r['data_status'],r['first_seen_at'])
        parsed=[]
        for meta in source.execute("SELECT * FROM metal_series WHERE provider='bls'"):
            values=[(r[0],r[1],r[2]) for r in source.execute('SELECT period,value,notes FROM metal_observations WHERE series_id=? ORDER BY id',(meta['id'],))]
            parsed.append((dict(meta),values))
        bls,_=engine.store('bls',parsed,None) if parsed else (0,0)
        r=source.execute("SELECT value FROM market_settings WHERE name='cross_history'").fetchone()
        if r:
            evidence=json.loads(r[0]);evidence.update(formal_applied_at=gm.now(),formal_added={'global_quotes':added,'metal_observations':bls});save(evidence)
    return {'global_added':added,'bls_added':bls}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',choices=['nbs','fed','bls','inventory']);p.add_argument('--pages',type=int,default=67);p.add_argument('--max-files',type=int,default=160);a=p.parse_args()
    import app;app.setup_database()
    print(json.dumps(import_nbs(a.pages,a.max_files) if a.source=='nbs' else import_fed() if a.source=='fed' else import_bls() if a.source=='bls' else inventory(),ensure_ascii=True,indent=2))
