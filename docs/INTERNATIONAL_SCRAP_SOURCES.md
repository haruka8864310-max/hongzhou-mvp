# 國際廢鋼來源查核（2026-11-10）

本輪先完成豐興／東京比較，再做來源補齊與研究。沒有把「看得到新聞」等同可免費批量保存，也沒有新增無資料國家卡片。

|市場／來源|真實可取得內容與限制|本輪處理|
|---|---|---|
|[東京製鐵官方購買頁](https://www.tokyosteel.co.jp/scrapprice/)|官方索引實列77份2025～2026年PDF。自行公告的工廠、原品級、數值；非出口H2、非FOB。版面／全文版權保留，沿用受限數值事實萃取，非整站授權；每次檢查robots、最多12份、不猜網址。|補上七廠與明示休止欄位格式；獨立庫成功保存2025年全部39期、2,360筆，2025-01-24～12-27。各廠歷史長度不同，東京灣岸較晚出現，休止欄不補價格。|
|[JISRI市場報告](https://www.jisri.or.jp/market)|H2地區區間出所為日刊市況通信社；東京灣FOB月均與美國Composite另列日本鐵源協會，並非JISRI獨立開放資料授權。三地區月平均自2023/04停止刊載。頁面文字與表格月份口徑亦不應任意對齊。|未取得第三方保存／再利用條件，不下載新聞全文或保存第三方區間，不拿東京二級冒充H2。|
|[日本官方貿易統計](https://www.customs.go.jp/toukei/sankou/howto/faq.htm)|官方統計可依引用規則標來源；HS申報單價是數量／價值推算，不能保證固定H2、港口與交貨條件。|本輪未新增固定H2／FOB連接器，避免用不同口徑冒充。|
|[BLS既有PPI](https://www.bls.gov/developers/)|已存在本機月度美國廢鋼PPI；免金鑰端點有年數與查詢限制。|直接進入新統一分析選單與國際摘要；明示指數，不當HMS美元／噸。|
|[USGS鐵與廢鋼](https://www.usgs.gov/centers/national-minerals-information-center/iron-and-steel-scrap-statistics-and-information)|[USGS自製資訊原則上公有領域](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits)，第三方內容例外。年報交貨複合均價是年度統計，部分價格註腳引用其他報價服務，不能直接以USGS名義概括所有數值授權。|沒有把年度Composite假裝即時HMS 80:20 CFR Taiwan；本輪不新增該現貨價。|
|[Fastmarkets廢鋼方法](https://www.fastmarkets.com/uploads/2024/02/fm-mb-ferrous-scrap-indicies.pdf)|HMS 80:20 CFR Turkey及美國／歐洲出身等是不同指標。公開方法文件不等於可免費保存其歷史報價；[官方價目清單](https://help.fastmarkets.com/metals-price-list)列出獨立價格代碼。|未繞過付費資料，土耳其CFR與美國CFR Taiwan仍未接入；不以BLS代替。|
|[中國國家統計局生產資料表](https://www.stats.gov.cn/sj/zxfb/202605/t20260506_1963594.html)|存在螺紋鋼HRB400E固定直徑等旬度調查價；不是期貨結算、不是廢鋼。公開發布涉及統計資訊服務中心／卓創資訊，需進一步核對下載授權、歷史清單及各商品原規格。|已查見實表與最新發布索引，尚未完成條款與穩定歷史下載驗收，故不宣稱中國連接器完成。|
|[韓國環境公團再生資源價格API](https://www.data.go.kr/data/15156640/openapi.do)|[官方目錄JSON](https://www.data.go.kr/catalog/15156640/openapi.json)列為免費JSON/XML、利用範圍不限，端點`apis.data.go.kr/B552584/reutilMrktPrcExmn`；仍需申請服務金鑰及核對月度分類、單位。這是再生資源調查，不能直接改稱鋼廠指定採購或進口CFR。|需要使用者自行申請的服務金鑰，未新增假的採購歷史。未寫入金鑰或送Git。|
|[韓國鋼鐵協會](https://www.kosa.or.kr/)、[越南鋼鐵協會](https://vsa.com.vn/)|公開統計／新聞入口不等同有可下載的固定品級CFR歷史。KOSA本輪網頁讀取沒有可解析價格表；VSA有產業報告與新聞，但未核對到開放授權的HMS/H2歷史端點。|未確認的價格不擷取、不造序列，保留既有合法匯入途徑。|

待確認：JISRI各第三方報價權利、US／Turkey現貨供應商使用許可、中國旬表穩定歷史下載及再利用條件、韓國API服務金鑰與實際分類。這些缺漏會進入研究特徵的missing／stale旗標，絕不填入替代商品或隨機值。
