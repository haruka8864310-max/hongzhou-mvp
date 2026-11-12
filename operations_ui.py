"""Route-separated navigation using the existing market chart and APIs."""
from pathlib import Path
from html import escape
import re
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router=APIRouter()
BASE=Path(__file__).parent
NAV=[('/','營運總覽'),('/customers','客戶管理'),('/purchases','收購／出貨'),('/fenghsin','豐興行情'),('/international','國際行情'),('/profits','毛利計算'),('/analysis','市場分析'),('/updates','資料更新與設定')]

def navigation():
    return '<button id="navToggle" aria-expanded="false" aria-controls="sideNav">☰ 選單</button><aside id="sideNav"><a class="brand" href="/">弘洲金屬<br><small>營運與市場決策系統</small></a><nav aria-label="主導覽">'+''.join(f'<a href="{url}" data-nav="{url}">{name}</a>' for url,name in NAV)+'</nav><p>本機資料 · 手動更新<br>金額：新台幣／重量：公斤</p></aside>'

def wrap_html(html):
    html=html.replace('</head>','<link rel="stylesheet" href="/assets/operations.css"><script src="/assets/navigation.js" defer></script></head>')
    return html.replace('<body>','<body class="with-sidebar">'+navigation(),1)

def page(title,content,section,scripts='/assets/operations.js'):
    html=f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>弘洲｜{title}</title><script src="{scripts}" defer></script></head><body><main data-page="{section}"><header><h1>{title}</h1><p>資料保存在此電腦；行情只在按下更新後取得。</p></header><p id="status" role="status" aria-live="polite"></p>{content}</main></body></html>'
    return wrap_html(html)

def input_field(key,label,type='text',attrs=''):
    return f'<label>{label}<input name="{key}" type="{type}" {attrs}></label>'

CUSTOMER_FIELDS=''.join(input_field(k,l,attrs=a) for k,l,a in [('name','客戶名稱','required maxlength="150"'),('tax_id','統一編號','pattern="[0-9]{8}|" maxlength="8"'),('contact','聯絡人','maxlength="100"'),('phone','電話','maxlength="80"'),('address','地址','maxlength="300"'),('main_items','主要品項','maxlength="300"'),('payment_terms','付款條件','maxlength="150"'),('tags','標籤（逗號分隔）','maxlength="300"')])+'<label>類型<select name="customer_type"><option>其他</option><option>供應商</option><option>買方</option><option>兩者</option></select></label><label>狀態<select name="active"><option value="true">啟用</option><option value="false">停用</option></select></label><label class="wide">備註<textarea name="note" maxlength="3000"></textarea></label>'

def render(section):
    if section=='overview':
        return page('營運總覽','<section><h2>營運摘要</h2><div id="overviewCards" class="cards"></div><p>毛利只列已分攤成本的出貨；未出貨成本另外顯示。</p></section><section><h2>開始操作</h2><div class="actions"><a class="button" href="/customers">管理客戶</a><a class="button" href="/purchases">新增收購</a><a class="button" href="/shipments">新增出貨</a><a class="button" href="/fenghsin">查看豐興行情</a></div></section>',section)
    if section=='customers':
        return page('客戶管理','<div class="toolbar"><input id="search" placeholder="搜尋名稱、統編、電話、標籤"><select id="activeFilter"><option value="active">啟用客戶</option><option value="inactive">停用客戶</option><option value="all">所有客戶</option></select><select id="typeFilter"><option value="">所有類型</option><option>供應商</option><option>買方</option><option>兩者</option><option>其他</option></select><button id="newCustomer">新增客戶</button></div><section><div class="scroll"><table><thead><tr><th>名稱／永久Key</th><th>聯絡資料</th><th>類型／品項</th><th>狀態</th><th>操作</th></tr></thead><tbody id="customersBody"></tbody></table></div></section><dialog id="customerDialog"><form id="customerForm"><h2 id="customerHeading">新增客戶</h2><div class="formgrid">'+CUSTOMER_FIELDS+'</div><p id="customerError" role="alert"></p><button type="submit">儲存客戶</button><button type="button" data-close="customerDialog">取消</button></form></dialog><section id="customerDetail" hidden><h2 id="detailTitle"></h2><p id="detailKey"></p><div id="customerTotals"></div><h3>全部相關收購／出貨</h3><div class="scroll"><table><thead><tr><th>日期／單號</th><th>類型／品項</th><th>淨重kg</th><th>金額</th><th>批次</th></tr></thead><tbody id="customerTrades"></tbody></table></div></section>',section)
    if section in ('purchases','shipments'):
        kind='buy' if section=='purchases' else 'sell';title='收購管理' if kind=='buy' else '出貨管理'
        filters='<div class="toolbar"><a class="button" href="/purchases">收購單</a><a class="button" href="/shipments">出貨單</a><select id="customerFilter"><option value="">所有客戶</option></select><label>起日<input id="startDate" type="date"></label><label>迄日<input id="endDate" type="date"></label><button id="queryTrades">查詢</button><button id="newTrade">新增'+('收購' if kind=='buy' else '出貨')+'</button><a id="exportLink" class="button">匯出Excel</a></div>'
        fields='<label>客戶<select name="customer_key" required></select></label>'+input_field('date','日期','date','required')+'<label id="batchField">收購批次<select name="purchase_id"></select></label>'+input_field('material','金屬品項','text','required maxlength="80"')+''.join(input_field(k,l,'number',attrs) for k,l,attrs in [('gross_kg','毛重（kg）','required min="0.001" step="0.001"'),('deduction_kg','扣重（kg）','required min="0" step="0.001" value="0"'),('unit_price','單價（元/kg）','required min="0" step="0.0001"'),('transport','運費（元）','required min="0" step="0.01" value="0"'),('processing','加工費（元）','required min="0" step="0.01" value="0"')])+'<label>付款狀態<select name="payment_status"><option value="unpaid">未付／未收</option><option value="partial">部分付款</option><option value="paid">已付清／已收清</option></select></label><label class="wide">備註<textarea name="note" maxlength="3000"></textarea></label>'
        return page(title,filters+'<section><p>一筆收購建立一個批次；可分次出貨。出貨必須選相同品項批次，不允許超賣。</p><div class="scroll"><table><thead><tr><th>日期／單號</th><th>客戶／品項</th><th>毛重／扣重／淨重kg</th><th>單價／金額</th><th>運費／加工費</th><th>批次／剩餘kg</th><th>付款／毛利</th><th>操作</th></tr></thead><tbody id="tradesBody"></tbody></table></div></section><dialog id="tradeDialog"><form id="tradeForm"><h2>'+title+'</h2><div class="formgrid">'+fields+'</div><p id="tradePreview"></p><p id="tradeError" role="alert"></p><button type="submit">儲存交易</button><button type="button" data-close="tradeDialog">取消</button></form></dialog>',section)
    if section=='profits':
        return page('毛利計算','<section><form id="profitForm" class="toolbar"><label>出貨起日<input name="start" type="date"></label><label>出貨迄日／庫存截止日<input name="end" type="date"></label><label>批次<select name="batch_key"><option value="">全部批次</option></select></label>'+input_field('sale_price','情境售價（元/kg，可留白）','number','min="0" step="0.0001"')+input_field('estimated_sale_cost','預估出貨運費＋加工費','number','min="0" step="0.01" value="0"')+'<button>計算毛利</button></form><p id="profitRule"></p><div id="profitCards" class="cards"></div><p id="legacyWarning"></p></section><section><h2>批次未分攤成本</h2><div class="scroll"><table><thead><tr><th>批次</th><th>品項</th><th>剩餘kg</th><th>未分攤成本</th></tr></thead><tbody id="lotCosts"></tbody></table></div></section><section><h2>已實現毛利／單筆出貨</h2><div class="scroll"><table><thead><tr><th>單號／日期</th><th>客戶</th><th>收入</th><th>分攤收購成本</th><th>運費＋加工費</th><th>已實現毛利</th></tr></thead><tbody id="shipmentProfit"></tbody></table></div></section>',section)
    if section=='updates':
        return page('資料更新與設定','<section><h2>行情更新</h2><p>預設手動更新。啟動、切換頁面與重新整理不會下載行情、匯入歷史或訓練模型。來源失敗保留歷史快取；完成檢查不等於最新報價已前進。</p><div class="scroll"><table><thead><tr><th>來源</th><th>狀態／進度</th><th>最後成功</th><th>保存筆數／本次新增</th><th>錯誤／結果</th><th>操作</th></tr></thead><tbody id="updateRows"></tbody></table></div></section><section><h2>資料保護</h2><button id="backupNow">建立本機備份</button> <a class="button" href="/api/portable-backup">下載移機備份</a><form id="restoreForm"><label>還原備份（還原前自動備份現有資料）<input type="file" name="file" accept=".db,.zip" required></label><button>還原備份</button></form><p id="backupStatus" role="status"></p><details><summary>進階設定與舊版工具</summary><a href="/advanced">行情參數、來源與CSV工具</a> · <a href="/legacy">原版資料工具</a> · <a href="/market-brief">原市場摘要</a></details></section>',section)
    if section=='analysis':
        return page('市場分析','<section><h2>跨國領先指標與預測研究</h2><a class="button" href="/prediction-lab">開啟現有預測實驗室</a><a class="button" href="/international">國際真實行情走勢</a><p>保留原跨市場特徵、時間順序回測、來源發布時間與合成測試隔離。</p></section><section><h2>目前市場因素</h2><div id="analysisFactors"></div></section><section><h2>豐興1～4週研究狀態</h2><div id="analysisForecast"></div><p>沒有合格回測及機率校準，不顯示漲跌機率；相關不代表因果。</p></section>',section,'/assets/operations-market.js')

def market_page(kind):
    html=(BASE/'templates'/'dashboard.html').read_text(encoding='utf-8')
    if kind=='all':return wrap_html(html)
    chart=re.search(r'<section id="fenghsin"[\s\S]*?</section>',html).group()
    signals='<section><h2>國際市場最新資料</h2><div id="internationalRows"></div></section>' if kind=='international' else '<section><h2>調價統計與來源</h2><div id="adjustments"></div><p>與前一筆已知報價比較；公告缺口不推定平盤。</p></section>'
    result=page('豐興行情' if kind=='fenghsin' else '國際行情',chart+signals+'<p id="futuresLimit"></p><p>TradingView Lightweight Charts™ · <a href="https://www.tradingview.com/">TradingView</a> · <a href="/assets/vendor/lightweight-charts/LICENSE">Apache-2.0</a></p>',kind,'/assets/operations-market.js')
    return result.replace('<script src="/assets/operations-market.js"','<script src="/assets/vendor/lightweight-charts/lightweight-charts.js" defer></script><script src="/assets/market-chart.js" defer></script><script src="/assets/operations-market.js"')

@router.get('/customers',response_class=HTMLResponse)
def customer_page():return render('customers')
@router.get('/purchases',response_class=HTMLResponse)
def purchase_page():return render('purchases')
@router.get('/shipments',response_class=HTMLResponse)
def shipment_page():return render('shipments')
@router.get('/profits',response_class=HTMLResponse)
def profit_page():return render('profits')
@router.get('/updates',response_class=HTMLResponse)
def update_page():return render('updates')
@router.get('/fenghsin',response_class=HTMLResponse)
def fenghsin_page():return market_page('fenghsin')
@router.get('/international',response_class=HTMLResponse)
def international_page():return market_page('international')
@router.get('/analysis',response_class=HTMLResponse)
def analysis_page():return render('analysis')
