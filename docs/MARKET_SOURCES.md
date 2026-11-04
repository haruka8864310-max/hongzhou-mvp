# 行情來源與權利研究

查核日期：2026-11-04。本版沒有自動連接器，不將網頁可見、免費註冊或延遲報價解讀成允許抓取及公司使用。手動／CSV 也須具備適用許可。

| 追蹤項目 | 官方／候選來源與條件 | 本版處理 |
|---|---|---|
| 豐興廢鋼、鋼筋 | [豐興](https://www.fenghsin.com.tw/)；週公告、品級及含稅口徑需確認，未確認資料重用條款 | 僅匯入公司有權使用的紀錄，不抓取 |
| 日本 H2／出口 | [日本鐵鋼連盟](https://www.jisf.or.jp/)及公司已授權報價；地區、FOB／CFR 與品質不可混用 | 無已確認的免費公司用資料流，手動／CSV |
| 美國廢鋼 | [USGS](https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information)提供歷史統計、年報及資料表，非即時成交價；須核對個別資料集及第三方引用權利 | 免費官方候選，尚無欄位轉換器，不把年報當即時價格 |
| COMEX HG | [CME 授權](https://www.cmegroup.com/market-data/license-data.html)與[政策](https://www.cmegroup.com/market-data/license-data/information-policies.html)包含員工內部顯示等用途，須區分顯示／非顯示、歷史與延遲權利 | 未有適用許可不串接，不使用網站內部報價端點 |
| LME 銅 | [FAQ](https://www.lme.com/en/about/faqs/market-data-faqs)、[訂閱](https://www.lme.com/subscriptions)；可見價格及隔日 XML 等產品有不同條件 | 不因為延遲而視為免費商用；現貨、三個月期分開 |
| SHFE 銅 | [版權聲明](https://www.shfe.com.cn/disclaimer/)、[交易規則](https://edu.shfe.com.cn/TradingAssistant/RulesIntroduction/518.html)載明資訊權利與商業使用限制 | 未取得適用許可不串接，記錄合約與含稅口徑 |
| 鐵礦砂／鋼鐵背景指標 | [World Bank 商品市場](https://www.worldbank.org/en/research/commodity-markets)、[資料授權](https://datacatalog.worldbank.org/public-licenses)；預設開放資料 CC BY 4.0，部分資料集另有條款。Pink Sheet 有月／年資料 | 合法免費候選；確認實際資料集、品級、第三方欄位與頻率後才導入，不冒充每日現貨 |
| USD／JPY／CNY 兌台幣 | [央行開放宣告](https://www.cbc.gov.tw/tw/cp-307-36676-DE020-1.html)採[政府資料開放第 1 版](https://data.gov.tw/license)，須註明出處及注意例外 | 優先候選，先提供 CSV；個別數列方向、頻率、發布時間與 API 穩定性尚待確認 |

每筆記錄包含行情日、價格、幣別、單位、規格、合約／口徑、來源名稱、HTTP(S) 網址、內部使用授權依據、含時區發布時間、本機更新時間與延遲狀態。今天匯入的舊資料不會被標成今日行情。授權註記由使用者提供，不代表系統驗證其權利。

待確認：公司既有報價及數據商許可；豐興品級、含稅與持平週；H2 交貨條件；美國品級；銅合約月份／收盤結算口徑；至少一年可追溯發布時間的豐興週資料。法律及契約權利未確認前不自動抓取，本研究不代替法律審查。
