# 首頁語義推薦

首頁新增「AI 語義推薦」頁籤，輸入「想找晚餐」後按「用 AI 找相關拼單」。使用既有免費本機 Qwen3 embedding 模型，將需求與已授權貼文內容、標籤及地點轉成向量，比較意思；不呼叫文字生成，不自動申請、發文或付款。

## 範圍與限制

- 保留「最新拼單」與原本「依我的偏好」，原版 SQL 與資料庫結構不變，無需 migration。
- 第一版從原推薦 RPC 依規則排序的前 64 筆可加入貼文重排。這不是全站向量檢索，UI 明示候選數及截斷狀態；沒有後續語義分頁。
- 混合分數為原規則分數 × 0.4，加上非負 cosine 相似度 × 60。地點原本是偏好加權，不是強制同一地點；此次沒有把它改成硬篩選。分數權重尚未經真人成效校準，相似度不是成功率或食品安全判定。
- 原 RPC 保護 session、雙向封鎖、沒興趣、取消、截止時間及剩餘名額。推論前後重新讀取；版本、內容、地點、費用、期限或容量改變時移除舊判讀，偏好版本變更要求重新載入。
- 額外每十秒呼叫唯讀的資格驗證 API，更新名額與可見性，不呼叫模型；驗證失敗隱藏舊結果。切帳號、離開前景或偏好版本改變丟棄舊分析與晚回應。
- 模型未設定、忙碌、失敗或向量空間改變時，明示回到新讀取的規則結果，不捏造語義理由；不轉付費服務。每程序同時只准一個首頁語義分析操作，沿用既有本機模型容量限制。
- 資料只送已設定的 loopback 本機模型；不讀私人聊天或偷揪需求，不保存查詢或新增雲端向量表。裝置模式納入 AI 路徑保護。

## 執行

沿用後端 `scripts/run_supabase_core.py`／裝置 runner，模型設定同現有偷揪語義功能：`STEALTH_SEMANTIC_ENABLED=true`、`STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED=true`，並有已安裝的 `STEALTH_EMBEDDING_MODEL`。此功能不下載模型或自動重啟正在執行的服務；更新後重新啟動後端、重新載入 App。

新 API：`POST /api/v1/recommendations/semantic` 接受 `{"query":"想找晚餐"}`；`POST /api/v1/recommendations/semantic/validate` 接受最多 64 個貼文 UUID 的 `ids` 與分析時的 `preference_version`，只重新驗證既有推薦資格。

## 驗證

`tests/test_feed_semantic.py` 覆蓋向量排序、候選上限、推論中滿團／過期／封鎖／編輯、偏好變更、身份失效、模型備援及輸入檢查；前端 `core-semantic-feed.test.tsx` 覆蓋明確觸發、非模型輪詢、備援與切帳號／背景／偏好變更的晚回應。

本輪完整後端 580 項、前端 51 組／225 項測試通過，TypeScript、變更檔 ESLint、Python Ruff 與 iOS Hermes JS 套件匯出通過。Windows 測試及打包使用工作目錄內隔離暫存路徑，避免系統暫存目錄權限問題。JS 匯出不代表完成原生簽章或手機全流程驗收。

`python scripts/smoke_feed_semantic.py --run-local` 僅用三筆合成公開貼文與已安裝本機模型，不連 Supabase。2026-10-22 真實 Qwen3 embedding 0.6B 結果：「想找晚餐」對活動剩餘便當約 0.492、冰紅茶約 0.362、文具約 0.296；便當排序第一。這是單一合成例子，不是推薦準確率或真人成效研究。可重跑證據存於忽略的 `.local/feed-semantic-smoke.json`。

尚待新版真人 Web／iPhone 全流程驗收；未部署、未修改共享資料庫、未串剩食份數與領取狀態。
