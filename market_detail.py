"""Separate attributed steel bulletins and detail views over existing stored data."""
import hashlib,json
from datetime import date,datetime,timezone,timedelta
from statistics import mean
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict,model_validator
import market,metal_engine as engine,metal_analysis as analysis

router=APIRouter(prefix='/api/detail')
SOURCES={'metaltrade':{'name':'基本金屬供需平台（來源CIP）','url':'https://chart.metaltrade.tw/ste/domestic/7/'},'tymetals':{'name':'同洋鋼鐵豐興開盤資訊','url':'https://www.tymetals.com.tw/news-detail/402/'}}
LABELS={'rebar':'鋼筋','scrap':'廢鋼','sections':'型鋼'}


class Bulletin(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True,allow_inf_nan=False)
    cadence:Literal['monthly','weekly']
    product:Literal['rebar','scrap','sections']
    period:str
    price:float|None=Field(default=None,gt=0,le=1e9)
    change:float|None=Field(default=None,ge=-1e8,le=1e8)
    source:Literal['metaltrade','tymetals']
    specification:str=Field(min_length=1,max_length=200)
    permission_note:str=Field(min_length=1,max_length=300)
    @model_validator(mode='after')
    def valid(self):
        today=datetime.now(engine.TAIPEI).date()
        try:
            day=datetime.strptime(self.period,'%Y-%m' if self.cadence=='monthly' else '%Y-%m-%d').date()
            if self.period!=day.strftime('%Y-%m' if self.cadence=='monthly' else '%Y-%m-%d'):raise ValueError()
        except ValueError:raise ValueError('月份須YYYY-MM，週牌價日期須YYYY-MM-DD')
        if day>today or self.cadence=='monthly' and self.period>=today.strftime('%Y-%m'):raise ValueError('不能輸入未來或未完成月份')
        if self.cadence=='monthly' and (self.source!='metaltrade' or self.product!='rebar' or self.price is None):raise ValueError('此月均來源僅支援鋼筋實際月均價格')
        if self.cadence=='weekly' and self.source!='tymetals':raise ValueError('週公告與月均來源必須分開')
        if self.price is None and self.change is None:raise ValueError('請填價格或調價金額；平盤填調價0，不填價格')
        return self


def reports():
    with market.connection() as c:r=c.execute("SELECT value FROM market_settings WHERE name='steel_bulletins'").fetchone()
    return json.loads(r[0]) if r else []


def report_key(r):
    text=json.dumps([r[k] for k in ('cadence','product','source','specification')],ensure_ascii=False)
    return 'r:'+hashlib.sha256(text.encode()).hexdigest()[:20]


def validate(c):
    saved=c.execute("SELECT value FROM market_settings WHERE name='steel_bulletins'").fetchone()
    if not saved:return
    rows=json.loads(saved[0])
    if not isinstance(rows,list) or len(rows)>5000:raise ValueError('豐興公告備份格式無效')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!=set(Bulletin.model_fields)|{'key','updated_at'}:raise ValueError('豐興公告欄位無效')
        value=Bulletin.model_validate({k:row[k] for k in Bulletin.model_fields})
        stamp=datetime.fromisoformat(row['updated_at'])
        if stamp.tzinfo is None or stamp>datetime.now(timezone.utc):raise ValueError('豐興公告更新時間無效')
        identity=(row['key'],row['period'])
        if row['key']!=report_key(value.model_dump()) or identity in seen:raise ValueError('豐興公告序列或日期無效')
        seen.add(identity)


@router.post('/steel')
def save_report(value:Bulletin):
    row=value.model_dump();row['updated_at']=datetime.now(timezone.utc).isoformat();row['key']=report_key(row)
    # Existing settings table is included in existing portable backup/restore.
    with market.connection() as c:
        saved=c.execute("SELECT value FROM market_settings WHERE name='steel_bulletins'").fetchone();rows=json.loads(saved[0]) if saved else []
        if any(r['key']==row['key'] and r['period']==row['period'] for r in rows):raise HTTPException(409,'此來源／規格／日期已有紀錄；未覆蓋舊資料')
        if len(rows)>=5000:raise HTTPException(400,'最多5000筆公告，請先備份並整理')
        rows.append(row)
        c.execute("INSERT INTO market_settings(name,value) VALUES('steel_bulletins',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(rows,ensure_ascii=False),))
    return {'ok':True,'key':row['key'],'message':'已保存手動核對的資料，與工程會、其他規格及頻率分開。'}


@router.get('/steel')
def steel():
    groups={}
    for r in reports():groups.setdefault(r['key'],[]).append(r)
    items=[]
    for key,rows in groups.items():
        r=max(rows,key=lambda x:x['period']);monthly=r['cadence']=='monthly'
        stale=r['period']<analysis.shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2) if monthly else (datetime.now(engine.TAIPEI).date()-date.fromisoformat(r['period'])).days>10
        items.append({**r,'name':'豐興'+LABELS[r['product']]+('歷史月均價' if monthly else '最新週牌價／調價'),'unit':'TWD/t','source_info':SOURCES[r['source']],'stale':stale,'count':len(rows)})
    return {'items':items,'sources':SOURCES,'automatic':False,'message':'兩個來源未確認可自動下載授權，僅連結與手動核對錄入；不保存新聞全文。平盤不代表已知絕對價格。'}


def datasets():
    result={}
    for s in engine.get_series():
        result['m:'+s['id']]={'id':'m:'+s['id'],'name':s['display_name'],'frequency':'monthly','unit':s['unit'],'currency':s['currency'],'specification':s['specification'],'source_url':s['source_url'],'source':engine.PROVIDERS[s['provider']]['name'],'notes':s['notes'],
            'points':[{'period':r['period'],'value':r['value'],'updated_at':r['retrieved_at']} for r in engine.observations(s['id'])]}
    for key,rows in market.grouped().items():
        r=rows[-1];item=market.INSTRUMENTS[r['instrument']]
        result['q:'+key]={'id':'q:'+key,'name':item['name'],'frequency':'weekly' if r['instrument'].startswith(('fh_','jp_')) else 'observed','unit':r['currency']+'/'+r['unit'],'currency':r['currency'],'specification':r['specification']+' · '+r['contract'],'source_url':r['source_url'],'source':r['source'],'notes':'依原始實際報價日，不補造日期或缺值。',
            'points':[{'period':x['date'],'value':x['price'],'updated_at':x['updated_at']} for x in rows], 'quote_rows':rows}
    for r in reports():
        key=r['key'];source=SOURCES[r['source']]
        if key not in result:result[key]={'id':key,'name':'豐興'+LABELS[r['product']]+('歷史月均價' if r['cadence']=='monthly' else '週牌價／調價'),'frequency':r['cadence'],'unit':'TWD/t','currency':'TWD','specification':r['specification'],'source_url':source['url'],'source':source['name'],'notes':'手動核對來源事實；月均、週公告及工程會訪價分開。','points':[],'announcements':[]}
        result[key]['announcements'].append(r)
        if r['price'] is not None:result[key]['points'].append({'period':r['period'],'value':r['price'],'updated_at':r['updated_at']})
    for s in result.values():
        s['points'].sort(key=lambda p:p['period'])
        if 'announcements' in s:s['announcements'].sort(key=lambda p:p['period'])
    for item in market.CATALOG:
        if not any(s.get('instrument')==item['id'] for s in result.values()):
            existing=next((s for s in result.values() if s['id'].startswith('q:') and s.get('quote_rows',[{}])[0].get('instrument')==item['id']),None)
            result['i:'+item['id']]=existing or {'id':'i:'+item['id'],'name':item['name'],'frequency':'weekly' if item['id'].startswith(('fh_','jp_')) else 'observed','unit':item['currency']+'/'+item['unit'],'currency':item['currency'],'specification':'尚無已匯入的同規格資料','source_url':item['url'],'source':'原始來源入口','notes':'請更新合法來源或手動匯入；沒有畫假圖。','points':[]}
    return result


@router.get('/catalog')
def catalog():return [{k:s[k] for k in ('id','name','frequency','unit','specification')} for key,s in datasets().items() if not key.startswith('i:')]


def converted(s,mode):
    if mode not in ('native','twd'):raise HTTPException(400,'計價方式不符')
    if mode=='native' or s['currency']=='TWD':return s['points'],s['unit'],0
    if s['currency']=='INDEX':return [],s['unit'],len(s['points'])
    points=[]
    if s['frequency']=='monthly':
        fx=analysis.month_fx()
        if s['currency']=='USD':
            for p in s['points']:
                if p['period'] in fx:points.append({**p,'value':p['value']*fx[p['period']]['rate'],'fx':fx[p['period']]})
        unit='TWD/'+s['unit'].split('/')[-1]
    else:
        groups=market.grouped()
        for row,p in zip(s.get('quote_rows',[]),s['points']):
            value=market.convert(row,groups)
            if value:points.append({**p,'value':value['value'],'fx':value})
        unit='TWD/t'
    return points,unit,len(s['points'])-len(points)


def ranged(points,months,frequency,anchor=None):
    if not points or months==0:return points
    end=anchor or points[-1]['period'];cut=analysis.shift(end[:7],-(months-1) if frequency=='monthly' else -months)
    if frequency!='monthly':
        import calendar
        y,m=map(int,cut.split('-'));day=min(int(end[8:10]),calendar.monthrange(y,m)[1]) if len(end)>7 else 1
        cut+=f'-{day:02d}'
    return [p for p in points if cut<=p['period']<=end]


@router.get('/view')
def view(series:str,months:int=12,mode:str='native',compare:str|None=None):
    if months not in (0,1,3,6,12,36):raise HTTPException(400,'期間須為1、3、6、12、36個月或0全部')
    all_data=datasets();s=all_data.get(series)
    if not s:raise HTTPException(404,'尚無此項歷史資料，請更新或手動錄入')
    original=s['points'];latest=original[-1] if original else None
    # Anchor to source latest, not an older last-available FX month.
    announcements=s.get('announcements',[]);last_report=announcements[-1] if announcements else None
    anchor=last_report['period'] if last_report else latest['period'] if latest else None
    converted_points,unit,missing=converted(s,mode);points=ranged(converted_points,months,s['frequency'],anchor)
    values=[p['value'] for p in points]
    change={'absolute':values[-1]-values[0],'percent':(values[-1]/values[0]-1)*100,'base':points[0]['period'],'end':points[-1]['period']} if len(values)>=2 else None
    comparison=[];compare_note='選另一指標後，以共同實際日期首筆100比較走勢；不同頻率不混用。'
    if compare:
        other=all_data.get(compare)
        if not other:raise HTTPException(400,'比較指標不存在')
        if other['frequency']!=s['frequency']:compare_note='頻率不同，不能直接對齊。請選擇同頻率指標；未將月價重複成週價。'
        else:
            # Native returns avoid incomparable currencies/physical units on one price axis.
            right={p['period']:p['value'] for p in other['points']};left=ranged(original,months,s['frequency'],anchor)
            common=[p for p in left if p['period'] in right]
            if len(common)>=2:
                a,b=common[0]['value'],right[common[0]['period']]
                comparison=[{'period':p['period'],'a':p['value']/a*100,'b':right[p['period']]/b*100} for p in common]
                compare_note=s['name']+'／'+other['name']+'，原幣走勢共同首期100；不同商品不代表套利價差，與主圖台幣模式分開。'
            else:compare_note='不足兩筆共同實際日期，無法比較；未補值。'
    stale=False
    if anchor:stale=anchor<analysis.shift(datetime.now(engine.TAIPEI).strftime('%Y-%m'),-2) if s['frequency']=='monthly' else (datetime.now(engine.TAIPEI).date()-date.fromisoformat(anchor)).days>10
    return {'series':{k:v for k,v in s.items() if k not in ('points','quote_rows','announcements')},'latest':latest,'latest_announcement':last_report,'points':points,'unit':unit,'missing_fx':missing,'change':change,'statistics':{'high':max(values),'low':min(values),'mean':mean(values),'samples':len(values)} if values else None,'stale':stale,'comparison':comparison,'compare_note':compare_note,
        'message':'至少兩筆實際價格才能畫折線；僅調價資訊不能還原絕對價格。' if len(points)<2 else '只連接真實觀察點，未插值或補造日行情。',
        'conversion_note':'台幣月價使用同月至少10筆日匯率平均；日／週報價用當日以前7日內匯率。缺匯率不補值，價格指數不能換成台幣價格。'}
