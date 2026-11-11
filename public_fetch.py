"""Small audited public downloads; no cookies, login, proxies or retry bypass."""
import ssl
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import certifi

AGENT='HongzhouMarketResearch/1.0'


class SourceError(ValueError):
    def __init__(self,url,message,status=None):
        super().__init__(message);self.url=url;self.status=status


def fetch(url,hosts,audit,limit=3*1024*1024):
    if urlsplit(url).scheme!='https' or urlsplit(url).hostname not in hosts:
        raise SourceError(url,'非核准HTTPS來源')
    entry={'url':url,'http_status':None,'error':None}
    audit.append(entry)
    try:
        ctx=ssl.create_default_context();ctx.load_verify_locations(certifi.where())
        with urlopen(Request(url,headers={'User-Agent':AGENT}),context=ctx,timeout=12) as response:
            entry.update(http_status=response.status,content_type=response.headers.get('Content-Type',''))
            if response.status!=200:raise SourceError(url,'HTTP不是完整200',response.status)
            if urlsplit(response.url).hostname not in hosts:raise SourceError(url,'拒絕跨站轉址',response.status)
            raw=response.read(limit+1)
        if len(raw)>limit:raise SourceError(url,'檔案超過限制',200)
        return raw
    except HTTPError as exc:
        entry.update(http_status=exc.code,error=str(exc))
        raise SourceError(url,f'HTTP {exc.code}：未繞過存取限制',exc.code) from exc
    except Exception as exc:
        entry['error']=str(exc)[:400]
        if isinstance(exc,SourceError):raise
        raise SourceError(url,f'{type(exc).__name__}：{exc}',entry['http_status']) from exc


def check_robots(home,download):
    url='https://'+urlsplit(home).netloc+'/robots.txt'
    try:raw=download(url)
    except SourceError as exc:
        if exc.status==404:return None,'robots HTTP404；未宣告規則，不等於授權'
        raise
    policy=RobotFileParser();policy.parse(raw.decode('utf-8-sig').splitlines())
    if not policy.can_fetch(AGENT,home):raise SourceError(home,'robots禁止此索引，未下載')
    return policy,'robots允許此公開路徑；使用權另依來源條款'
