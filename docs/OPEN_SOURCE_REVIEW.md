# 指定工具評估

查核日期：2026-11-04。程式授權不等於市場資料授權。以下四個工具本版均未加入執行依賴、未複製程式。

| 工具 | 授權／成熟度查核 | 相依與相容性 | 決策 |
|---|---|---|---|
| [Lightweight Charts](https://github.com/tradingview/lightweight-charts) | Apache-2.0，約 17.8k stars；有[官方版本紀錄](https://tradingview.github.io/lightweight-charts/docs/release-notes)；使用須保留 LICENSE、NOTICE、TradingView 署名及連結 | 瀏覽器 JS／Canvas，可消費 FastAPI JSON，本身不帶行情，可本機打包 | 最適合後續整合；本版先用原生 Canvas 完成流程，專業縮放與游標互動待後續，不引用 CDN |
| [Darts](https://github.com/unit8co/darts) | [Apache-2.0](https://github.com/unit8co/darts/blob/master/LICENSE)，約 9.6k stars，有文件／測試 | [依賴](https://github.com/unit8co/darts/blob/master/pyproject.toml)含 NumPy、pandas、SciPy、scikit-learn、statsmodels 等，深度模型另加 torch。Python 可整合，訓練應隔離背景工作 | 延後；先驗證無新增依賴的頻率基準及資料品質，不在歷史不足時引入大型模型 |
| [vectorbt](https://github.com/polakowo/vectorbt) | [Apache-2.0 加 Commons Clause](https://github.com/polakowo/vectorbt/blob/master/LICENSE.md)，含 Sell 限制，不能視為純 Apache 開源；選用依賴可能另有限制 | NumPy／pandas／Numba 等向量回測相依；可由 Python 研究模組呼叫，方向命中率不需要此策略引擎 | 不導入；內部用途仍依實際條款，後續散布／收費需重審。現版回測非策略損益回測 |
| [OpenTerminal](https://github.com/ygf-ui/openterminal) | [MIT](https://github.com/ygf-ui/openterminal/blob/main/LICENSE)；查核頁顯示 20 commits、0 stars，未有足夠維護保證證據 | web／server 堆疊與目前簡單 FastAPI 不同；README 涉及 reverse-engineered 行情端點，其資料權利不因 MIT 成立 | 只研究資訊組織方式，不整套搬移、不承襲行情端點、不引入另一個 server |

Stars 是查核時頁面快照，不是穩定性保證；未做完整提交頻率／安全稽核，不宣稱已充分確認所有專案的維護品質。

本版市場邏輯使用 Python 標準函式庫與現有 FastAPI／Pydantic，前端用原生 DOM／Canvas。FastAPI、Pydantic、pytest 為 MIT；Uvicorn、HTTPX 為 BSD-3-Clause；python-multipart 為 Apache-2.0。HTTPX 僅放開發測試需求。實際安裝版本授權應保留套件 metadata／LICENSE，完整版本鎖定與相依供應鏈審查仍待後續。

前端整合測試新增 **jsdom 26.1.0（MIT）**，只在 `devDependencies`，並提交 `pnpm-lock.yaml` 固定版本。它用於 DOM／表單事件與 API 測試，正式程式不載入。安裝時有一項傳遞依賴 `whatwg-encoding@3.1.1` 棄用提示；目前測試通過，未以更換整套套件來消除提示。測試套件完整依賴的安全稽核仍待後續。
