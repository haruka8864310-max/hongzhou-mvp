# 全球廢鋼第一階段（2026-11-10）

## 第一批：盤點與既有歷史修復

現有 FastAPI、SQLite、templates、assets、market_sources、metal_engine、metal_analysis、market_detail 均保留。修改前 Python 基線68項通過，1項既有Starlette/httpx棄用警告。沒有重建專案或替換公司資料庫。

| 功能／模組 | 盤點狀態 | 本階段處理 |
|---|---|---|
| market_sources 日參考匯率 | 已有央行與Frankfurter連接器；後者先前出現522 | 重新實測預設來源，區分抓取時間與報價日期 |
| metal_engine 月資料 | World Bank、BLS廢鋼／廢銅、工程會 | 保留修訂；重測下載、解析、SQLite與前端 |
| market_detail | 真實歷史、比較、手動豐興週／月 | 補3年選項，保留單月／缺匯率原因 |
| 全球國別廢鋼 | 只有台灣／日本／美國部分舊入口 | 新增統一來源目錄及可用／手動／未授權狀態，不產生假報價 |
| metal_analysis | 描述性走勢、相關、雷達、基準研究 | 保留，預測標明尚未完成；本階段不訓練模型 |
| 公司資料與備份 | 原資料、移機、還原及localhost限制 | 全保留；只新增結構，先備份升級 |
| legacy營運介面 | 既有ERP類頁面與API | 保留程式與資料，不在主要導覽展示 |

既有頁面／API清單可由 `app.app.routes` 與 `/openapi.json`檢視；測試含原營運資料保留、來源錯誤、SQLite還原、搜尋與代表規格、時間序列、卡片點擊。原頁面保留但主要導覽會聚焦總覽、國家、走勢、比較、來源、研究六項。

## 來源查核

- 東京製鐵：https://www.tokyosteel.co.jp/scrapprice/ 。官方按日期提供購入表與檢收規格，工廠、品級與到貨條件不可混合；公開讀取不等於自動再利用授權，本階段僅連結與合法手動／CSV。未把H2自動當FOB出口價。
- 日本鐵回收工業會：https://www.jisri.or.jp/ 。可見市場、生產與庫存，未確認可免費自動保存的價格授權。不能把鋼產量或廢鋼庫存當價格。
- 日本財務省貿易統計：https://www.customs.go.jp/toukei/info/tsdl_e.htm 。有e-Stat下載入口；須按HS碼、國別、重量、金額與單位核對。金額／重量的申報均價不是H2固定規格牌價；尚未實作其自動價格連接器。
- USGS：https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information 。公開年／月報，正遷移ScienceBase；PDF／Excel須核對自製與第三方資料，不把全美平均當HMS CFR Taiwan；本階段來源入口及手動匯入。
- worldsteel：https://worldsteel.org/data/about-our-data/ 。公開摘要可註明來源引用，但完整月歷史Excel涉及付費與內部使用條件，不接付費平台或繞過登入。產量不等於廢鋼價。
- World Bank、BLS、央行、工程會：沿用已查核固定官方下載端點；最新實測另列，不用其他商品冒充地區廢鋼。

中國、韓國、越南、印度、土耳其的指定商品尚無已確認免費自動來源時，保留有標準規格欄位的手動／CSV入口及機構來源連結，清楚標示連接器未實作／需核對授權。海運成本與焦煤亦不以其他指標代替。

## 後續批次

第二批新增統一行情結構、來源版本、保守的當時可得查詢與國別介面。第三批實測公開來源與前端、備份還原及完整回歸。未驗證項目不列為完成。第二階段豐興1～4週預測另行處理。
