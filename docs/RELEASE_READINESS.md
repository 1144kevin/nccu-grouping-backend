# 開發交付與 iPhone 驗收表

最新更新（2026-10-20）：裝置 HTTPS API 可明確啟用受保護的本機 AI，仍未在 Mac／iPhone
實測；Ollama 不開放區網。回報管理已套用開發專案並依本人指定授權帳號，
支援查核／結案／回覆及二次確認取消被檢舉拼單；正式申訴與其他內容處置仍待做。
模型改為單程序有界 FIFO（執行一筆、等候兩筆、等待五秒），本機 500 貼文與最後名額演練完成。
完整後端 464 tests、App 142 tests 通過；詳見 `COMPETITION_DEMO.md` 與 `CORE_SUPABASE_DEV.md`。
沒有部署或產出 TestFlight build，雲端向量候選發現、Apple 原生模型仍未完成。

本輪環境：Windows localhost + Supabase 開發專案 `axdwbfhrkbovgrbngrrh`；後端 `main`、前端 `kevin_dev`。沒有部署、EAS 建置、推送 Git、消耗 OpenAI 額度或代替真人確認拼單。

## 功能狀態

| 功能 | 現況 | 接續工作 |
| --- | --- | --- |
| Google Web／信箱登入、續期 | 已串接；Google 登入先前由本人驗證 | 團隊外信箱需自訂 SMTP；沒有在本輪寄信 |
| 發布、主揪編輯／取消、申請審核、截止前退出 | 已串接，含版本、重試與最後名額併發測試 | 實機重跑兩帳號流程 |
| 單張拼單附圖 | 私人 Storage、後端縮圖／移除 EXIF、交易發布、未發布副本清理 | HEIC 實機轉換、已發布圖片生命週期、多圖／聊天附件 |
| 文字聊天、已讀、真實未讀數 | 已串接，前景輪詢 | Realtime／背景推播尚未接；語音仍未上傳 |
| 個人偏好、封存／靜音、沒興趣、截止提醒 | 本人雲端資料，切換帳號隔離 | 截止提醒目前只在前景頁面更新 |
| 搜尋、過往紀錄、公開個人資料 | 已串接；私人訊息只搜尋有權限的群組 | 較大資料量索引與效能測量 |
| 問題回報／檢舉 | 私人回報、授權管理者查核／結案／公開回覆、二次確認取消被檢舉拼單、私有稽核及版本重試 | 訊息遮蔽、平台停權、正式申訴、處理時限、聯絡資料 |
| 一般 App 使用者封鎖 | 雲端本人名單／雙向互動限制已接入貼文、申請、聊天、搜尋、圖片與偷揪；封鎖清除舊畫面快取 | 既有成員保留必要紀錄與退出權；實機驗收及正式治理仍待處理 |
| 偷揪分身／需求／配對／全員確認成團 | 已串接；改版清空同意、同版全員同意才建單與聊天室 | 配對規模、跨日、更多分類與地點、長時間運作測試 |
| 自然語言補問 | 真正免費本機 Qwen3.5 4B，原句依據可檢查，明確套用後才可儲存；另12組反例修正後全通過 | 標準合成20例19例通過；分類／行程仍可能漏，不能當成完整語義準確率 |
| 費用／分工草案 | 免費規則規劃器；使用已知費用與本人志願，不猜價格 | 目前不是 LLM 代理協商；仍須每人親自確認 |
| iPhone Google 登入 | 原生 PKCE／固定回跳程式完成，預設停用 | Supabase 精確 allowlist、Mac 原生 build、實機登入 |
| Apple 登入或同等隱私登入選項 | 尚未實作 | 發行前依 Login Services 規範檢查適用性；一般信箱登入不自動代表滿足隱藏信箱選項 |
| iOS 打包 | Windows 離線 JS／資源 export 成功 | Xcode 編譯、簽章、安裝與實機測試尚未執行 |
| TestFlight | release 檢查與接續文件已備妥 | 可達 API、Apple Team／App 記錄／簽章、驗收及上傳；目前無 TestFlight build |
| 帳號刪除 | App 內預覽、近期登入、手動 DELETE、可續作圖片清理、實際 Auth／關聯資料刪除已驗證 | Mac／iPhone 驗收，正式備份保留與支援流程 |
| 完整隱私／使用條款、內容治理 | 尚未完成 | 正式保留政策、管理者處理與申訴流程、公開聯絡資訊 |
| Apple 裝置模型、APNs、付款 | 未實作 | 各自獨立規劃；不得宣稱已支援 |

## Mac／iPhone 驗收順序

詳細設定見 [iPhone 與 TestFlight](IPHONE_TESTFLIGHT.md)。下面只列驗收，並非已執行結果。

1. Mac 安裝專案指定 Node／Python／Xcode，建立自己的虛擬環境與依賴；先在 Simulator + Mac localhost 測試。不要複製 Windows `.venv`，不要把管理金鑰放進 App。
2. 準備可達的 API 才測實體 iPhone。現在 localhost adapter 不可直接當上線服務；本輪沒有放寬 loopback、CORS 或 ATS，也沒有開防火牆／穿透通道。
3. 系統 Google 登入視窗：取消保持未登入；成功回到 App；重複回呼不能重複交換；重開 App 續期；登出後不能恢復舊身份。
4. 用兩個專用帳號：發布未來拼單，另一人申請，主揪接受，滿團拒絕再加入，截止前退出釋出一席；有人申請後不得偷改條件；取消後聊天室唯讀。
5. 選圖：拒絕權限、取消、HEIC、旋轉方向、超大圖、斷線、重試、發布中切換頁面／背景、關 App 後草稿副本清理。驗證發布後重開仍見附圖，其他帳號看不到未發布副本。
6. 聊天：登入者自己的未讀／已讀、切換帳號、離開群組失去存取、切到背景不送已讀、回前景重新同步。封存／靜音不能刪其他人的資料。
7. 偷揪：預設不開啟；需求補問只產生草案；修改條件後所有人重新確認；未全員同意不建單；重試不重複；示範人不能同意成團。電腦模型不可達時允許手動填寫，不轉付費 API。
8. 最後處理正式圖示、公開支援聯絡方式、隱私政策／資料刪除與內容治理，完成 release preflight，才在 Mac 簽章 Archive。iOS JS export 不代表簽章／TestFlight 已完成。

## 資料流盤點（供團隊確認隱私說明）

| 資料 | 目前處理位置／可見範圍 |
| --- | --- |
| Google／信箱身份 | Supabase Auth；App 只持有自己 session；公開資料不含信箱 |
| 發布內容、集合地點、附圖 | Supabase 開發專案；貼文公開給已登入使用者，附圖經短效簽署連結讀取 |
| 私人聊天 | 同拼單主揪／已接受成員可讀；退出即失去存取；原有訊息仍保留 |
| 分身、需求、回報、偏好 | 資料庫 RPC 限本人；配對僅提供必要成團摘要，不公開原始私人敘述 |
| 自然語言輸入 | localhost API → 本機 Ollama；沒有呼叫 OpenAI；目前不把模型對話寫進雲端資料庫 |
| 裝置精確位置 | 只有使用者另行同意定位時才使用；本機流程可手填測試地點；本輪未取得真人定位 |
| 日誌／備份／刪除 | 仍須正式定義保留期間、備份處理與使用者刪除流程；目前不可承諾「所有資料即時永久刪除」 |

正式發行前應以實際 production 設定與第三方 SDK 資料行為重新盤點，不能直接把此表當成已完成的對外隱私政策。

## 官方發行依據

[Apple UGC 規範 1.2](https://developer.apple.com/app-store/review/guidelines/#user-generated-content) 要求內容過濾、回報與及時回應、封鎖濫用者及公開聯絡方式；本 App 只有收到回報並不足以代表完成內容治理。[Apple 帳號刪除說明](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion) 說明提供帳號建立的 App 也須提供 App 內發起刪除。實際審核仍由 Apple 決定。

[Expo SDK57](https://docs.expo.dev/versions/v57.0.0/) 與 [本機原生開發](https://docs.expo.dev/guides/local-app-development/) 是 Mac 建置接續依據。Windows export 只驗證 JavaScript／資源，不代替 Xcode 與裝置驗收。

[Apple Login Services 4.8](https://developer.apple.com/app-store/review/guidelines/#login-services) 對第三方主要帳號登入的同等隱私登入選項有要求與例外；是否適用需按 App 的最終登入設計檢查，目前不宣稱已符合。

## Windows 已完成的交接準備

Mac setup／檢查、只含已提交歷史的 Git bundle 產生器、模擬器／內網TLS裝置／未來HTTPS雲端三種 App profile、預設關閉的限制裝置TLS runner、帳號設定連線狀態、未使用原生權限收斂已備妥。指令見 IPHONE_TESTFLIGHT.md，逐項紀錄見 DEVICE_ACCEPTANCE.csv。沒有新增部署、LAN監聽或系統憑證；Mac原生編譯、iPhone信任與連線、公開API runtime／部署、Apple登入、政策與治理仍待後續。

## 雲端後端準備更新

本機 AI 更新：需求理解／向量／候選判讀三個可替換接口、Qwen 免費語義排序及固定合成評估已完成。12 題檢索 top-1 12/12，6 題判讀通過；仍需真人授權測試與更大盲測集。SQLite 支援語義優先建立配對；Supabase 開發模式僅排序合法既有摘要。App 已接上手動語義排序、判讀理由與待確認事項，132 項前端測試及離線 Web export 通過；本次未再次登入真人帳號驗收。雲端向量候選發現、Apple／OpenAI adapter 仍未完成；不影響原配對、方案編輯與全員確認。詳細步驟見 CORE_SUPABASE_DEV.md 的「本機語義配對與可替換 AI 接口」。

獨立cloud runner、檢查專用環境、嚴格TLS代理合約、精確CORS、單程序限流／body限制、無憑證健康probe、非root鎖檔容器設定與公開URL App profile匯出已完成。沒有部署、修改Supabase或實際build Docker。第一版限制單程序／單實例；保留原本local模式與本機模型，雲端只提供手動需求及免費規則草案。主機／TLS／代理實際驗收、Docker Linux build、操作備份與iPhone流程仍待後續。
