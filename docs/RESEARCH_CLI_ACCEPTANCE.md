# 可執行開源研究交付（2026-11-11）

本輪集中修補可執行整合與資料管線，沒有新增行情卡片、國家頁或資料管理表單。沿用豐興價格中心與三張原資料表。

## 1. 安裝、授權與真實呼叫

`requirements.txt`現已包含`-r requirements-research.txt`；原`start_windows.bat`也檢查skforecast／LightGBM，缺少時依專案依賴安裝，不再只放一個未被主安裝流程使用的選配清單。BAT保持ASCII及CRLF，Windows啟動測試通過。

實際執行`python -m pip install -r requirements.txt`成功；現有venv已安裝skforecast **0.26.0**、LightGBM **4.7.0**，Python **3.11.9**。兩套件wheel metadata均要求Python>=3.10；numpy2.4.6、pandas2.3.3、scikit-learn1.9.1等既有依賴未重複安裝替代品，`pip check`無衝突。

- [skforecast授權](https://github.com/skforecast/skforecast/blob/main/LICENSE)：BSD-3-Clause。
- [LightGBM授權](https://github.com/microsoft/LightGBM/blob/master/LICENSE)：MIT。
- [廢鋼研究參考授權](https://github.com/hyunwoomoon1990/steel-scrap-price-prediction/blob/main/LICENSE)：本輪GitHub官方API再次確認MIT。其新聞語料的商業授權不隨程式MIT開放，不下載、不使用；不抄錄研究準確率。

真正套件呼叫：

|檔案／函式|實際用途|
|---|---|
|`scrap_forecast.classifier()`|建立LightGBM `LGBMClassifier`三分類器|
|`scrap_forecast.walk_forward()`|呼叫skforecast `TimeSeriesFold.split()`做expanding folds及h期間gap，逐折呼叫LightGBM `.fit()`、`.predict()`|
|`scripts/research_cli.fit_candidate()`|真正`.fit()`訓練候選模型，輸出booster文字、特徵／類別／版本／SHA256清單，不用不安全pickle|
|`scrap_forecast.forecast()`|驗證足量、基準增益及時效後才執行`.predict_proba()`；未達條件保持null|

skforecast在此負責時間切分，LightGBM負責方向分類；沒有把skforecast價格回歸區間錯當方向機率。

實測發現LightGBM Windows native `save_model()`不支援本機含「桌面」的部分絕對路徑，已改用`model_to_string()`及Python Unicode檔案寫入，並以含中文絕對路徑驗證成功。

## 2. 執行命令

在原專案目錄用PowerShell：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\research_cli.py doctor
.venv\Scripts\python.exe scripts\research_cli.py weekly
.venv\Scripts\python.exe scripts\research_cli.py train
.venv\Scripts\python.exe scripts\research_cli.py backtest
```

預設唯讀`%LOCALAPPDATA%\HongzhouAssistant\hongzhou.db`，派生週表保存`research\weekly-analysis.sqlite3`，每次報告放`research\runs\<唯一ID>\report.json`。不建立空白正式庫、不呼叫初始化或還原、不覆寫原報價。週表及報告是由主庫可重建的研究產物，現有主庫備份格式不變；搬移若需要保留報告檔，也應保留整個research資料夾。

可用`--data-dir`指定**既有**獨立資料庫目錄、`--output-dir`指定研究輸出、`--as-of`指定含時區截止時間。成功退出碼0；訓練／回測因資料不足退出碼2，但仍保存完整診斷；套件、輸入或I/O錯誤為1。不要把退出碼2理解成已訓練成功。

獨立的軟體引擎驗證命令：

```powershell
.venv\Scripts\python.exe scripts\research_cli.py verify-engine --output-dir .test-runs\engine-check
```

這是**合成數學分類測試，不是行情，不是正式豐興回測**，不寫入行情表、不進入預測API，候選模型位於分離的engine-verification子目錄。本輪實際CLI四個期間各訓練108列，分別完成55／54／53／52折；確認真正呼叫套件、訓練、預測及產生文字模型。測試成績不可宣稱市場準確率。證據：`.test-runs/engine-unicode-release/engine-verification/e209a9e7cfc04a99abfc7750fcc065f5/report.json`。

## 3. 統一週表與防洩漏

`weekly_research.prepare()`直接使用現有`fenghsin_facts`、`global_quotes`、`metal_observations`的adapter，固定豐興廢鋼／東京岡山二級，加入可用日本、美國PPI及USD／JPY匯率。預測API和CLI共用同一批按origin讀取的版本，不以兩套不同週表算結果。

`weekly_observations`保存run、週、origin、來源序列、商品口徑、幣別、單位、原頻率、原報價日期、發布時間／日期、首次取得、版本取得、known_at、觀察ID、來源網址、價格及資料年齡。無發布時間保持null，不以報價日期偽造發布時間。

每天參考值最多沿用7日、月資料95日、年度370日；週／調價日預設35日。只取不晚於origin的原觀察，月度值不是新造的每週報價。當時未取得的列標`not_known_at_origin`，**price=NULL**；來源未接入為`source_unavailable`，不以替代商品填值。資料庫CHECK禁止非known或known_at晚於origin的價格入表。原始庫不改動，研究側庫採append-only run；拒絕寫入非本工具建立的同名SQLite檔。

## 4. 真實資料與本次結果

正式現有庫讀取結果：

|預設研究指標|實際筆數|最早|最新|
|---|---:|---|---|
|豐興廢鋼絕對價格|21|2026-06-22|2026-11-09|
|東京岡山／二級|75|2025-01-24|2026-10-22|
|東京全部工廠／品級|4,544|2025-01-24|2026-10-22|
|美國BLS廢鋼PPI|117月|2017-01|2026-09|
|USD/TWD|259|2025-10-07|2026-10-30|
|JPY/TWD|259|2025-10-07|2026-10-30|

H2／FOB、US HMS、CN等未接入來源明確缺值，不將BLS改名HMS。匯率最新日期已陳舊，不能假稱當前可用匯率。

正式命令已依序執行：weekly成功保存每run **231列**（21個目標週×11個指標位置）；三個run共693列，非法未來價格列0。最後一次週表狀態：97列當時未知、126列來源未接入、4列當時已知、4列缺值／過期。這些不是231筆可訓練樣本。豐興目前只有當前1個可得週，其未來標籤尚未出現。

因此train與backtest四個期間均為 **合格訓練0、模型測試0**，準確率、召回率、混淆矩陣與機率保持未知；每期間還缺至少52筆合格訓練與20筆有效測試，各類訓練至少5筆。這些是合格樣本缺口，不是聲稱再抓52個任意歷史日期就一定足夠。舊日期行情的首次取得較晚，不能回填為過去已知。

正式回測報告：`%LOCALAPPDATA%\HongzhouAssistant\research\runs\04dd168636c744138ea5ed0863ce322d\report.json`；訓練診斷：`...\4777bfa527fd422aab095fb2cc3e444b\report.json`。目前仍可算事後歷史訊號，1～4週領先比較樣本數20／19／18／17，明確不是預測準確率。

## 5. 評分與首頁

新增永遠平盤、上一期方向、多數類別的基準。`classification_metrics()`輸出測試筆數、準確率、各類support／recall及混淆矩陣；零測試不輸出準確率，某類無真實樣本時recall為null。基準全樣本評分與模型同折評分分開，不以不同樣本數聲稱勝出。正式發布門檻也納入上一期方向基準。

首頁自動讀預設比較並呼叫`GET /api/scrap-analysis/predict`，不需先選多個序列。舊比較設定若錯指型鋼，會回復可用豐興廢鋼／東京預設；即使進階比較改其他商品，預測目標仍固定豐興廢鋼。原比較欄位移入進階展開，保留既有價格中心。首頁加入已有回測API的歷史表現表，顯示缺口與召回；沒有新增頁面、行情卡片或資料輸入表單。

前端HTTP／DOM驗收直接記錄`/api/scrap-analysis/predict`請求，驗證未操作序列前即出現四個期間、五列歷史表現、無可靠預測理由；同時保留原幣／台幣／標準化曲線及失敗保留。Console錯誤0。這是HTTP／DOM證據，未聲稱本輪完成實機視覺驗收。

## 6. 檔案與測試

|檔案|修改前→修改後|
|---|---|
|`requirements.txt`、`requirements-research.txt`、`start_windows.bat`|研究依賴分離→主安裝流程納入並實際檢查imports|
|`scripts/research_cli.py`|不存在→五個可執行命令、候選模型／報告、安全拒絕不足樣本|
|`weekly_research.py`|不存在→統一週表與獨立SQLite保存|
|`scrap_forecast.py`|缺上一期方向及召回→完整基準評分、樣本缺口、共享PIT版本|
|`scrap_analysis.py`|僅research路由→predict別名及週資料狀態、保存已知發布日期|
|`templates/scrap-analysis.html`、`assets/scrap-analysis.js`|預設需操作／無歷史表現→自動固定目標與預測API、進階比較、歷史表現|
|`app.py`、`run_local.py`、`assets/market.js`|舊服務契約→research-cli-2，避免前端誤連舊API|
|`tests/test_research_cli.py`、`tests/scrap-ui.test.cjs`|新增CLI／週表／四期間真套件驗證，前端加入自動predict及錯誤舊設定回歸|

97項Python測試通過（獨立測試庫），前端11項既有及1項真實行情整合驗收通過；pip check無衝突。原正式資料庫未遷移、覆蓋或還原；測試資料不寫進正式行情。研究產物與模型不提交Git。
