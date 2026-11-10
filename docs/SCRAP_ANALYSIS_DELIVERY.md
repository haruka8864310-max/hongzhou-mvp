# 國際廢鋼比較與豐興研究分階段紀錄

## 第一階段：統一資料與真實比較

修復原因：`fenghsin_facts`未被舊`global_quotes`選單讀取；舊比較API拒絕不同頻率且只取完全相同日期。新增`scrap_analysis.py`唯讀整合三張現有表，月資料排除已鏡像到global的重複。保留原表、版本與來源；不把月價當廢鋼、指數當成交價。

新`templates/scrap-analysis.html`、`assets/scrap-analysis.js`由`app.py`整合首頁及「市場分析」，同一個元件移至所選頁面，不維護兩份互相矛盾的表單。預設豐興廢鋼與岡山工場／二級；原幣雙軸、基期100、台幣價格皆可使用。報價、來源、首次取得、換算匯率日期與資料年齡保留。設定存`market_settings.scrap_analysis`，重啟仍保存。順便修正舊手動表單與價格中心重複的`fhProduct/fhPeriod` DOM ID，舊表單改用legacy前綴。

公告柱狀圖移至摺疊「公告紀錄」內，新增日期／調價／來源時間軸，主價格折線不受影響。

實測獨立庫`.test-runs/scrap-integration`（真實既有行情SQLite副本，無假行情）：原幣及台幣曲線豐興21點／東京21點；共同基期100為21點／19點，基期前的東京觀察不混入標準化曲線。HTTP／DOM／Canvas 794條繪製線段，三種模式、兩頁切換、來源連結、連線失敗保留圖表皆通過。Python87 passed。

瀏覽器工具實測失敗：`The browser access host is unavailable`。未將DOM驗收聲稱為視覺實機驗收；此項尚未完成。

歷史走勢以目前已取得版本呈現；週對齊為週末以前最近觀察，附年齡且受門檻限制，沒有生成每日行情。**此為事後快照描述，不是歷史當時可得資訊集**。月資料以月底代表期末，並未宣稱月底已發布。預測與回測另使用實際取得／發布時間，不能用這種描述性對齊回填模型。

## 第二階段：東京歷史及來源查核

`japan_sources.py`新增明確七廠／八廠含休止欄位解析；價格以欄位座標對應表頭，未明示休止的空價不准左移或補值。新增`POST /api/japan/history?year=2025`，網頁進階設定可輸入年度，逐批最多12份，每次至少60秒，僅取官方索引實列檔案。

正式服務獨立下載結果（不是把測試庫覆蓋正式庫）：

|年度|成功PDF日期數|數值列數|最早|最新|
|---|---:|---:|---|---|
|2025|39|2,360|2025-01-24|2025-12-27|
|2026|36|2,184|2026-01-08|2026-10-22|
|合計|75|4,544|2025-01-24|2026-10-22|

官方索引共77份，仍有兩份被安全拒絕：2026-04-16表頭跨行，2026-05-23含尚未公布新據點價格的空欄。沒有宣稱已完成這兩期或填出價格；來源狀態保留失敗網址與原因，成功歷史仍可用。最新官方表10/22距本次日期19日，不能標即時。

2025年度全部39份已在獨立庫及正式庫分別下載解析，無假行情。各廠歷史長度不同，未把新設／休止據點填滿75期。來源與其他國家未接入原因詳見[來源查核](INTERNATIONAL_SCRAP_SOURCES.md)。JISRI第三方H2／FOB、US及Turkey現貨、China旬度資料、Korea與Vietnam指定採購價尚未完成連接器；BLS月指數及World Bank既有資料直接納入選單，未冒充HMS或中國廢鋼。

正式庫寫入前備份`hongzhou-20261110-190001-349274.db`。逐列比較備份：market_quotes777列、metal_series37列、metal_observations2,885列、fenghsin_facts99列完全保留；global_quotes原5,152列原值保留、追加至8,352列。公司表亦保留。豐興三品項仍各21個絕對報價日。

## 第三階段：可運作的領先研究及時間順序模型

`scrap_forecast.py`直接做四個期間的三分類（1／2／3／4週，含14與28天），不是把他國模型權重套到豐興。每週以日曆週分組，沒有該週豐興觀察就不生成目標。領先表比較國際指標本週變化與豐興未來h週相對本期的累計方向；有樣本數、相關、方向一致率、前後半段相關及逐筆ID證據。至少8組且非固定變化才顯示相關；持平也計入一致率，不能誤稱預測準確率。

正式當前資料的描述性領先樣本為20／19／18／17組，r約0.156／0.189／0.190／0.076；前後半段方向不穩定。這不是因果或可交易優勢。

模型特徵包含豐興1／2／4週變化、所選國際指標及可用H2／FOB／US實價／US指數／CN鋼材／USD與JPY匯率的變化、1～4週落後項、缺值與過期旗標。尚未接入的來源是None與missing=1，不用其他商品替代。原幣特徵與圖表模式獨立。

每個歷史origin重新讀取`datasets(as_of=origin)`，必須取得時間／首次取得／已知發布時間都不晚於origin；訓練標籤亦須當時已知。修訂依當時版本讀取。使用skforecast `TimeSeriesFold`，expanding window、h週gap、重訓、無隨機切分；LightGBM直接多分類，固定超參數與random_state只控制重現，不生成假機率。

比較模型、永遠平盤、多數類別及僅豐興特徵三個基準；回傳成對準確率、混淆矩陣、逐折訓練標籤最晚可得時間及特徵使用程度（非因果／非漲跌方向）。至少52筆訓練、每類5筆、20筆回測；模型需超過所有三種基準至少5個百分點，且對平盤的成對方向檢定p<0.0125（四期間門檻），最新資料亦不可過期，才允許輸出研究機率。機率未作獨立校準，仍不是保證。

**目前正式資料21個豐興報價日皆為最近取得，嚴格歷史特徵訓練0、回測0；四個期間均顯示「尚無可靠預測」，probabilities=null。** 不將今日快照冒充過去已知資訊。網頁可看缺少來源、訓練／回測數、信心、預計目標週、模型更新時間；未建模時更新時間明示未建模。較長歷史、可信發布／取得版本及持續累積仍必要。

## 開源工具與環境

- [skforecast](https://github.com/skforecast/skforecast) 0.26.0，[BSD-3-Clause](https://github.com/skforecast/skforecast/blob/main/LICENSE)，支援Python>=3.10；目前Python3.11.9。整合其時間序列切分，分類器由LightGBM實作，不假稱skforecast回歸區間是漲跌機率。
- [LightGBM](https://github.com/microsoft/LightGBM) 4.7.0，[MIT](https://github.com/microsoft/LightGBM/blob/master/LICENSE)，有Windows wheel，已實際安裝與跑四期間回測測試。只採LightGBM，未重複裝XGBoost。
- [廢鋼研究參考](https://github.com/hyunwoomoon1990/steel-scrap-price-prediction)原始LICENSE確認MIT，README明示韓國三家媒體語料為商業授權且不可再散布。已讀requirements、README及baseline清單／concat_xgb程式，只參考時間順序與結構化特徵概念；不複製新聞資料、TF-IDF、爬蟲或研究中的固定參考分數，不把韓國研究準確率當豐興成績。
- 選配依賴放`requirements-research.txt`，核心App不需ML依賴也能看走勢。實際安裝numpy2.4.6、pandas2.3.3、scikit-learn1.9.1、scipy1.17.1、numba0.68.1等上游依賴；未裝NLP、torch、tensorflow或新聞收集工具。`pip check`無相依衝突；上游授權隨套件保留。

## 最後驗收與操作

1. 執行原`start_windows.bat`、開`http://127.0.0.1:8765/`。首頁豐興中心保留，下面直接顯示國際摘要與預設比較。比較設定與固定工廠／品級會存入原SQLite。
2. 選原幣／基期100／台幣；來源、報價日及資料年齡在摘要及展開表格。缺歷史匯率時逐筆說明，不能用今日匯率回填。台幣換算不消除交貨與品級差異。
3. 按「分析目前兩個指標」，或側欄「預測研究」；四個領先週數與四個預測期間可操作。目前明示無可靠預測，圖表仍可使用。
4. 展開「公告紀錄」查看時間軸；柱狀圖已移入其中。進階對齊設定可逐批補指定年度官方歷史。

Python最終全套93項（包含新增月資料adapter去重測試），測試使用獨立資料庫。四個期間的LightGBM／skforecast流程使用明確標示的純合成函式測試資料，各自至少20折，驗證無未來特徵／標籤洩漏；這些成績未呈現在正式App，也不是正式行情回測準確率。

真實HTTP／DOM／Canvas最終測試涵蓋三模式、首頁／分析／研究頁、來源連結、設定、失敗保留及原豐興三品項圖表。獨立庫新比較原幣／台幣21／27點、共同基期100為21／19點；來源增補後原幣多出的早期觀察只在原始走勢呈現，不能當共同基期。Console錯誤0。瀏覽器實機仍受工具host unavailable阻擋，未宣稱視覺驗收完成。

重現：`python -m pytest -q --basetemp=.test-runs/scrap-shipping`；`node --test tests/market-ui.test.cjs`；設`HONGZHOU_LIVE_UI_URL`為獨立驗收服務後執行`tests/scrap-ui.test.cjs`及`tests/fenghsin-ui.test.cjs`。`scripts/check_live_scrap_analysis.py`會自建新庫、下載真實資料、驗證三模式／研究／備份及重啟，報告只寫忽略的`.test-runs`，不碰正式庫。

本次真實端到端腳本已執行，報告位於`.test-runs/live-scrap-analysis-9ci7hy9k/acceptance.json`：全新庫取得豐興99筆事實、東京首批12期768筆、匯率768筆；三種比較模式皆有至少兩筆實價，四期間無可靠機率，重新進入App lifespan後資料仍存在。此腳本只抓首批來源，不代表該新庫已補齊正式庫的75期。

主要新增檔案：`scrap_analysis.py`、`scrap_forecast.py`、`templates/scrap-analysis.html`、`assets/scrap-analysis.js`、`requirements-research.txt`、`scripts/check_live_scrap_analysis.py`及對應Python／DOM驗收測試。修改`app.py`、`run_local.py`、`market.html`、`assets/market.js`、`japan_sources.py`、豐興公告模板／JS、舊手動表單ID與導覽；資料結構仍版本6，沒有重建、刪除或覆蓋正式資料庫。
