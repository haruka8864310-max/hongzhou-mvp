# 空白市場與 Failed to fetch 修復驗收

日期：2026-11-04。沿用原專案，沒有新增業務頁面、帳務／庫存／毛利模組或公網服務。

## 診斷證據

- 當次唯讀檢查沒有執行中的 Python 服務，8765／相關預覽埠沒有監聽；不能據此認定使用者先前一定使用哪個網址。實際瀏覽器控制服務不可用，無法讀取使用者原頁面的 DevTools。
- 舊 `start_windows.bat` 在啟動 Uvicorn 前就開瀏覽器，沒有 readiness 檢查或持久日誌；服務啟動失敗後仍可能留下可點擊的舊頁面。
- 以原始 `market.html` 和原 JavaScript 重現：`Cannot read properties of null (reading 'addEventListener')`。原因是原 HTML 的設定區由 FastAPI 插入，直接開檔案／靜態伺服器缺少 `settingsform`，頂層事件綁定在初始化前中止。部分已綁定的按鈕仍會對錯誤來源 fetch，產生原始 Failed to fetch。
- 初始 API 使用 Promise.all，任一讀取失敗會使卡片整批不渲染。空歷史選單未說明缺資料原因，無資料仍能觸發研究請求。

以上是程式可重現的問題及當次本機狀態，不冒稱已讀到使用者原瀏覽器 Console／Network。

## 修復

- `run_local.py` 固定綁定 127.0.0.1，先檢查同版本健康端點與 SQLite，再開瀏覽器。連接埠已被不同程式／版本／資料目錄使用時拒絕誤接；啟動失敗保留日誌並不開頁面。
- 前端啟動先核對協定、當前 origin、健康回應及完整 HTML，再綁定介面。file://／靜態伺服器／舊版服務有繁體中文診斷及重新連線入口，不再出現未捕獲的 null 綁定錯誤。
- API 使用同一個網頁 origin，分辨連線失敗、逾時、非 JSON、HTTP 狀態錯誤；頁面顯示可操作的原因，Console 保留原始錯誤與實際方法／URL。沒有以隱藏原始問題代替接通服務。
- 行情讀取與來源狀態獨立處理，來源狀態失敗不會使所有卡片消失；歷史讀取失敗有自己的說明。
- 無資料選單有可見原因並停用；無豐興資料時不能送出評估，即使強制事件也由前端守門。後端仍維持資料不足回應，不捏造預測。
- `/market.html`、`/index.html` 經 FastAPI 會導回完整首頁；相對資產路徑使直接開檔案也能執行啟動診斷。

## 真實更新、保存與分析驗收

使用 `.test-runs/repair-live-20261104` 獨立資料庫，以新啟動器在 **http://127.0.0.1:8765** 啟動服務。關閉背景排程以確保資料確實由按鈕取得，不讀寫正式資料。

執行前端 DOM 整合：

```powershell
$env:HONGZHOU_LIVE_UI_URL='http://127.0.0.1:8765'
$env:HONGZHOU_LIVE_UPDATE='1'
$env:HONGZHOU_NETWORK_REPORT='.test-runs/repair-live-20261104/network-report.json'
node --test tests/live-rates-ui.test.cjs
```

結果：

- 實際觸發網頁更新按鈕，POST `/api/market/sync` 從央行官方新 API 成功取得 777 筆真實日參考，三個幣別各 259 筆；資料與網路報告均在忽略的驗收目錄，不提交真實報價數值。
- 最新資料仍截至 2026-10-30，來源資料集更新日 2026-11-02，正確標為延遲。來源、報價日期、本機取得時間、幣別與單位皆保留；不捏造逐筆發布時間。
- 銷毀第一個 DOM 並重新 GET 首頁，三個幣別筆數一致；歷史選單有三組資料，Canvas 收到實際線段，產生 241 個對齊比較日。
- 無豐興歷史時顯示繁體中文原因、停用評估；強制事件不送 POST `/research`，不出現 Failed to fetch。
- 前端請求均同一 origin `http://127.0.0.1:8765`，18 筆 API 請求全部 HTTP 200、DOM Console 錯誤零。伺服器 access log 對應請求均 200，未見 traceback。

本次仍無法使用實際瀏覽器控制服務（`The browser access host is unavailable`）。以上 Console／Network 為真實 HTTP + jsdom 的請求／錯誤記錄，非 Edge／Chrome DevTools 截圖；Canvas 無繪圖測試 context，不能宣稱已完成實際瀏覽器視覺驗收。

## 回歸測試

- Python 測試含健康端點、首頁別名、啟動器拒絕錯誤版本／資料目錄，以及既有來源／備份／分析測試。
- DOM 情境涵蓋直接檔案、錯誤服務埠、連線中斷、來源狀態 API 503、空資料、輸入／匯入／比較與永久設定。
- 詳細最後執行數量見 VERIFICATION.md 頂端；沒有將先前測試等同於使用者實機驗收。

## 尚未完成

豐興、COMEX、LME、SHFE、日本 H2 與美國廢鋼自動資料尚未取得適用免費公司用來源。豐興研究必須先有合法週歷史，不能用匯率替代。來源失敗時保留既有值、上次成功與失敗原因。權利與頻率限制見 MARKET_SOURCES.md。
