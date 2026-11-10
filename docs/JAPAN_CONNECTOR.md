# 日本連接器及現有來源修復（2026-11-10）

## 接入範圍與使用條件

### 東京製鐵：已實作並下載

來源：[官方購入頁](https://www.tokyosteel.co.jp/scrapprice/)、頁面明列之官方PDF及[檢收規格](https://www.tokyosteel.co.jp/assets/docs/scrapprice/kbkikaku.pdf)。

網站有All Rights Reserved，沒有聲稱取得其整份網站／版面／資料庫授權。這次與先前僅因頁尾版權就一律不接入的判斷不同：依日本[文化庁指引](https://www.bunka.go.jp/seisaku/chosakuken/seidokaisetsu/chosakukensha_fumei/)「單純事實或資料」與著作表達的區分，連接器僅萃取東京製鐵自行公開的工廠、品級、適用日與數值事實供本機研究，不複製／再散布PDF版面、圖表、說明全文或新聞。這是限縮處理範圍的工程判斷，並非東京授予全面資料授權；後續對外散布需另查。

每次先檢查robots。實站robots.txt為HTTP404，沒有宣告規則；這不等於取得著作授權。遇到禁止、403、驗證、跨來源跳轉或非正常檔案不繞過。只允許固定官方host及索引上實際列出的價格PDF；不猜測歷史URL、不遍歷其他網站。背景每日檢查，手動至少間隔60秒，每次最多12份，最新一份核對修訂，其餘逐批補入尚無資料的歷史。

首輪實際取得12份官方表，每份8個工廠×8個重要原品級，共768筆、64條獨立序列，每條12個觀察日，範圍2026-07-30至2026-10-22。未宣稱已取得完整1年或3年；選擇較長期間仍只顯示真正已有的點，資料可逐批累積。

品級保留電特A、特A、特級、一級、二級、級外、シュレッダーA/C。**官方二級不改稱H2**。8個工廠及海上／陸上合併欄位按原表分開，非出口FOB。只解析第一頁絕對價格，第二頁調價表不當價格。表頭、8欄順序、重要品級、日期或數值格式改變即拒絕該檔，已成功歷史保留。只有公告日期、沒有發布時分時，另顯示日期，published_at仍NULL；首次取得以真正完成下載解析後的系統時間記錄，不回填為公告日。

原始PDF網址保存在版本origin並於歷史逐筆連結，source_url採穩定官方入口，避免每次PDF網址變動導致同一工廠／品級裂成不同序列。數值修訂追加版本，不覆蓋原公司資料。

### 日本鐵回收工業會：沒有自動保存第三方H2區間

[市場頁](https://www.jisri.or.jp/market)及[價格推移頁](https://www.jisri.or.jp/kakaku)可公開讀取。地區H2範圍註明出所為日刊市況通信社，屬第三方彙整資料，未確認其保存／再利用條件，故連接器不下載入庫。頁面亦記載2023年4月起停止舊的東京／中部／關西三地區月均刊載；目前價格推移表仍有區間，不混為舊平均值。範圍端點與「中心價」不擅自取平均、補成單一成交價。

此來源實站頁可讀，但資料入庫0筆，原因為第三方使用條件，不是宣稱已建立H2連接器。未抓取會員／付費內容或新聞全文。日本其他官方統計若後續接入，亦須標明與固定H2牌價不同。

## 現有來源實際資料（獨立SQLite查詢）

最後隔離目錄`.test-runs/live-japan-qdjfo9vh`由`scripts/check_live_japan.py`建立；另有首輪`.test-runs/live-japan-dk9x1129`交叉核對。以下筆數為觀察值，不將相同日期的版本重複算成價格歷史：

| 來源／商品 | 觀察筆數 | 最早 | 最晚／最新 |
|---|---:|---|---|
| 東京製鐵8廠×8品級 | 768 | 2026-07-30 | 2026-10-22 |
| JISRI第三方H2區間 | 0 | 無 | 未接入 |
| World Bank銅 | 802 | 1960-01 | 2026-10 |
| World Bank鋁 | 802 | 1960-01 | 2026-10 |
| World Bank鎳 | 802 | 1960-01 | 2026-10 |
| World Bank鐵礦砂一致現貨口徑 | 214 | 2009-01 | 2026-10 |
| BLS美國廢鋼指數 | 117 | 2017-01 | 2026-09 |
| BLS美國廢銅指數 | 117 | 2017-01 | 2026-09 |
| 工程會鋼筋：18規格／地區 | 18 | 2026-04 | 2026-04 |
| 工程會H型鋼：9規格／地區 | 9 | 2026-04 | 2026-04 |
| 工程會鋼板：4規格 | 4 | 2026-04 | 2026-04 |
| USD/TWD | 256 | 2025-10-13 | 2026-10-30 |
| JPY/TWD | 256 | 2025-10-13 | 2026-10-30 |
| CNY/TWD | 256 | 2025-10-13 | 2026-10-30 |

工程會停在4月的原因：實際官方CSV調查時間僅民國11504，解析為2026-04；每條固定規格只有一個月。政府開放目錄標「每月更新」不是完整歷史承諾，現有三個下載皆為快照，未找到可驗證的官方歷史下載清單。不是前端日期過濾錯誤，也不能用抓取日期改寫報價月份。介面依實際最新月份動態提示，不把2026-04寫死成永久狀態。

## 網頁操作與修改

- 首頁「一鍵更新行情」會更新已可用來源。國家頁只優先顯示已取得價格，未接入項目收進原因清單。
- 日本→東京製鐵→選工廠與原品級，預設只顯示一張對應卡片。點「看歷史走勢」可查看12個真實調價日及原始PDF連結，並選原幣／台幣、1／3／6／12／36月及全部。少於兩個有效實價時顯示原因，不畫假折線。
- 手動錄入改為彈出視窗；固定規格、單位、原始來源可帶入，進階欄位展開。未知品級需先核對，不能靠表單預填冒充。CSV收進「進階資料管理」。普通看行情無需填表。
- 豐興只有調價消息時，標示缺少基準絕對價格；不以平盤取代價格。
- 核心新增`japan_sources.py`；整合`global_market.py`、`market_sources.py`、`global_catalog.py`、`app.py`；UI修改`assets/global.js`、`templates/global.html`、`templates/global-source-panel.html`及相關樣式。原有大型來源表單檔保留但不再作主要面板。
- 新增pypdf 6.x（BSD-3-Clause，Python3.11相容），原專案沒有PDF解析器，沒有安裝重複圖表或AI套件。BAT依賴檢查保留ASCII／CRLF。

## 驗收、失敗案例與啟動

首輪真實下載12／12份成功，價格PDF實站失敗0份；robots.txt真實HTTP404另列，不冒充價格失敗。離線、robots禁止、破損表頭、缺價與日期不符為明確標記TEST的故障注入；驗證失敗時不覆寫既有128筆合成歷史與上次成功時間。JISRI是條件未確認而未接入，不當作網路故障。合成測試資料只存在獨立庫。

Python測試含64欄位對應、不同觀察日同序列、節流、來源失敗保留資料、備份還原。真實HTTP／DOM驗收8廠／8品級選擇、只顯示一個固定規格、東京製鐵歷史、原幣36個月銅圖、台幣缺匯率原因、比較、手動彈窗及來源失敗保留圖。Canvas以繪圖呼叫驗證，原生瀏覽器目視驗收仍須分開說明。

最後結果：Python78項通過，既有與新HTTP／DOM前端12項通過，Console錯誤0，pip check通過。原生工具回報`The browser access host is unavailable`，未完成Edge／Chrome目視驗收。正式服務已在原資料目錄獨立下載768筆，沒有導入測試庫或合成價格；接入前先備份既有公司DB。

啟動仍為`start_windows.bat`，網址`http://127.0.0.1:8765`。更新程式後需重啟服務並Ctrl+F5。驗收腳本`.venv\Scripts\python scripts/check_live_japan.py`永遠使用新隔離資料目錄，輸出`acceptance.json`與SQL實查結果；不替換公司DB。

尚未開始其他國家新連接器、AI或日本變動後豐興1～4週反應研究。需要足量、同規格豐興絕對週價及當時可得版本，現在不產生預測機率。

## Changed files

- `AGENTS.md`
- `README.md`
- `app.py`
- `assets/global.js`
- `assets/market.css`
- `assets/market.js`
- `docs/OPEN_SOURCE_REVIEW.md`
- `docs/VERIFICATION.md`
- `global_catalog.py`
- `global_market.py`
- `japan_sources.py`
- `market.py`
- `market_sources.py`
- `requirements.txt`
- `run_local.py`
- `scripts/check_live_japan.py`
- `start_windows.bat`
- `templates/global-analysis.html`
- `templates/global-overview.html`
- `templates/global-source-panel.html`
- `templates/global.html`
- `tests/conftest.py`
- `tests/global-ui.test.cjs`
- `tests/test_japan.py`
