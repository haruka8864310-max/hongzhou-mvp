"""Reviewed market identities; entries without a connector never claim a price."""
COUNTRIES={'TW':'台灣','JP':'日本','US':'美國','TR':'土耳其','CN':'中國','KR':'韓國','VN':'越南','IN':'印度','GLOBAL':'國際輔助指標'}


def item(id,country,name,grade,term,currency,unit,url,provider=None,reason=None,frequency='weekly'):
    return dict(id=id,country=country,name=name,grade=grade,delivery_term=term,currency=currency,unit=unit,url=url,provider=provider,frequency=frequency,
                reason=reason or ('已有官方連接器；下載結果、實際報價日與過期狀態分開顯示。' if provider else '尚未確認可免費自動取得的同品級價格；請查閱來源並以有權使用的資料手動或CSV匯入。'),
                license='沿用既有官方來源查核條件' if provider else '自動再利用授權未確認；連結僅供查閱，手動錄入需填使用依據')


CATALOG=[
 item('tw_scrap','TW','豐興廢鋼','需填實際收購品級','廠收條件需核對','TWD','t','https://www.fenghsin.com.tw/'),
 item('tw_rebar','TW','豐興鋼筋週牌價','需填實際鋼筋規格','出廠／稅別依來源','TWD','t','https://www.fenghsin.com.tw/'),
 item('tw_sections','TW','豐興型鋼週牌價','需填型鋼規格','出廠／稅別依來源','TWD','t','https://www.fenghsin.com.tw/'),
 item('tw_rebar_monthly','TW','豐興鋼筋歷史月均','來源盤價規格','月均非週牌價','TWD','t','https://chart.metaltrade.tw/ste/domestic/7/',frequency='monthly'),
 item('jp_tokyo','JP','東京製鐵廢鋼採購','各工廠官方原品級，不改名H2','工廠購入；非FOB','JPY','t','https://www.tokyosteel.co.jp/scrapprice/','tokyo',reason='官方自刊數值事實的受限萃取；保留來源，不複製PDF版面、全文或第三方資料。每次最多12期，遇robots禁止即停。',frequency='observed'),
 item('jp_h2','JP','日本H2地區區間','H2，地區及區間端點分開','廠前實勢；非東京製鐵牌價','JPY','t','https://www.jisri.or.jp/market',reason='公開區間標明出所為日刊市況通信社，第三方再利用條件未確認，未自動下載保存。2023年4月起已停止三地區月均刊載。',frequency='monthly'),
 item('jp_fob','JP','日本H2出口FOB','H2','FOB 日本指定港','JPY','t','https://www.customs.go.jp/toukei/info/tsdl_e.htm',reason='官方貿易統計可下載，但HS申報均價不是固定H2牌價；尚未建立授權與口徑相符的自動連接器。'),
 item('us_hms','US','美國HMS 1/2','HMS 1/2（比例另填）','指定交貨條件','USD','t','https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information'),
 item('us_shredded','US','美國Shredded','Shredded（規格另填）','指定交貨條件','USD','short_ton','https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information'),
 item('us_cfr_tw','US','美國廢鋼CFR Taiwan','HMS／貨櫃規格另填','CFR Taiwan 指定港','USD','t','https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information'),
 item('us_legacy','US','美國既有廢鋼報價','依原始規格','依原始合約','USD','short_ton','https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information'),
 item('us_index','US','美國廢鋼價格指數','WPU1012；1982=100','國內PPI指數，非成交價','INDEX','index','https://api.bls.gov/publicAPI/v1/timeseries/data/','bls',frequency='monthly'),
 item('tr_hms','TR','土耳其HMS 1/2 80:20','HMS 1/2 80:20','CFR Turkey 指定港','USD','t','https://www.worldsteel.org/',reason='未接入授權的CFR廢鋼報價；worldsteel僅供產業背景，不是此價格來源。'),
 *[item(id,'CN',name,grade,'中國市場／指定交貨與稅別','CNY','t','https://www.stats.gov.cn/',reason='尚未確認對應規格的合法自動價格連接器；政府統計入口不等於已取得該期貨／現貨價。') for id,name,grade in [('cn_rebar','中國螺紋鋼','規格／合約另填'),('cn_hrc','中國熱軋捲板','厚度／規格另填'),('cn_iron','中國鐵礦砂','品位／合約另填'),('cn_coking','中國焦煤','焦煤規格／合約另填')]],
 item('kr_scrap','KR','韓國鋼廠採購與進口','廢鋼品級另填','工廠／進口條件另填','USD','t','https://www.kosa.or.kr/'),
 item('vn_hms','VN','越南進口HMS','HMS比例另填','CFR Vietnam 指定港','USD','t','https://vsa.com.vn/'),
 item('vn_h2','VN','越南進口H2','H2','CFR Vietnam 指定港','USD','t','https://vsa.com.vn/'),
 item('in_scrap','IN','印度進口廢鋼','品級另填','CFR India 指定港','USD','t','https://steel.gov.in/'),
 item('in_demand','IN','印度鋼材需求指標','指標定義另填','需求統計，非價格','INDEX','index','https://steel.gov.in/',frequency='monthly'),
 *[item('pcc_'+k,'TW','工程會'+name+'歷史參考','固定代表規格，詳細另列','訪價快照；非豐興價','TWD','t','https://data.gov.tw/dataset/'+dataset,'tw_'+k,frequency='monthly') for k,name,dataset in [('rebar','鋼筋','6820'),('hbeam','H型鋼','6577'),('plate','鋼板','6578')]],
 *[item('global_'+k,'GLOBAL','國際'+name+'月均','World Bank指定商品','國際月均，非指定國廢鋼','USD','dry_t' if k=='iron_ore' else 't','https://www.worldbank.org/en/research/commodity-markets','worldbank',frequency='monthly') for k,name in [('copper','銅'),('aluminum','鋁'),('nickel','鎳'),('iron_ore','鐵礦砂')]],
 item('fx_usd','GLOBAL','美元／台幣','1USD兌TWD','日參考','TWD','TWD per USD','https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01','fx',frequency='daily'),
 item('fx_jpy','GLOBAL','日圓／台幣','1JPY兌TWD','日參考','TWD','TWD per JPY','https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01','fx',frequency='daily'),
 item('fx_cny','GLOBAL','人民幣／台幣','1CNY兌TWD','日參考','TWD','TWD per CNY','https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01','fx',frequency='daily'),
 item('jpy_per_usd','GLOBAL','美元兌日圓（1USD=XJPY）','同日同來源交叉換算','參考匯率，非成交','JPY','JPY per USD','https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01','fx',frequency='daily'),
 item('freight','GLOBAL','海運成本','航線／船型另填','運價條件另填','USD','USD per t','https://unctad.org/',reason='尚未接入免費授權的指定航線運價，不能用商品價格代替。'),
 item('steel_output','GLOBAL','粗鋼生產量','國別／統計範圍另填','產量，非價格','NA','million_t','https://worldsteel.org/data/about-our-data/',reason='公開摘要可註明來源引用，完整月歷史Excel有訂閱條件；本階段不接付費平台。',frequency='monthly'),
]
BY_ID={r['id']:r for r in CATALOG}
for record in CATALOG:
    if record['id'].startswith('pcc_'):
        record['reason']='工程會官方檔為訪價快照；以實際報價月份為準。下載成功不代表有最新週價或完整歷史，未找到可驗證的官方歷史下載清單。'
