# 偷揪五小時工作紀錄

- 開始：2026-10-15 16:44 Asia/Taipei；預計時段結束：21:44。
- 已確認：既有後端只有 health endpoint；14 張 ORM 模型存在，無 initial migration。前端不改動。
- 安全範圍：本機新增程式／文件／合成資料與測試；不部署、不呼叫外部 AI、不連真實資料庫。
- 環境：Windows PATH 原本無 Python/uv/Docker/psql；已使用內建 Python 3.12 在專案建立 `.venv`，經套件下載授權後依現有 lockfile 安裝依賴。新增 tzdata 以支援 Windows 時區。沒有安裝系統服務或修改防火牆／驅動。
- 完成：`STEALTH_PLAN.md` 定義資料庫、API、agent、配對與兩週順序；`STEALTH_LOCAL.md` 說明操作與限制。

## 第一階段實際成果（16:44 開始，尚未滿五小時）

- 既有 14 張模型保留，新增 7 張 PostgreSQL ORM 模型（含配對工作）；凍結兩份 migration。只離線產生 SQL，尚未套用任何資料庫。
- 偷揪表 RLS 預設拒絕直接存取，proposal fingerprint／formation 唯一約束、member 對 signal owner 的複合外鍵。
- 獨立 SQLite 持久化 adapter，BEGIN IMMEDIATE 保護狀態轉換；不讀寫原 DATABASE_URL。
- agent opt-in、日配額、安靜時段、版本控制；生活訊號需本人確認，最多十筆有效訊號。
- 共需／互補兩至三人配對，依地點、分類、時間、預算、飲食聲明、封鎖與 agent 授權過濾。
- 私有提案、接受／拒絕、撤銷失效、期限重驗證；全員同意後仍需主揪確認。
- 訊號建立 idempotency、重送 payload 衝突；正式成立只一次，重疊提案無法重複使用訊號。
- 本機成團寫入一筆 local_post＋聊天室成員，不冒充已接既有 PostgreSQL Post API。
- 訊號與 match job 同交易保存，API 背景回呼嘗試配對；CLI 可在重啟後重試，generation 防止誤刪較新的工作。
- 離線草稿 provider 清楚標示 `is_llm=false`；不呼叫外部 AI，不消耗 OpenAI 額度。
- 本機功能預設關閉，僅 local 環境／loopback、token hash 身份驗證，拒絕 Origin 請求；秘密測試憑證 gitignored。
- 合成 CLI demo 完成三方提案→全員同意→成團，未啟動伺服器、未使用真實使用者資料。

## 驗證與待辦

- 前端未修改；未部署、未 push、未連真實 DB、未呼叫付費服務。
- 第一階段完整 pytest：73 passed；Ruff 與 git diff --check 通過。API 契約匯出為 `docs/stealth.openapi.json`（10 個 path、13 個 operation）。
- 第三方套件有 Starlette/httpx 與 anyio deprecation warning，不是業務測試失敗；不為壓掉警告任意升級既有依賴。
- 待完成：後端 PostgreSQL repository 設計與離線驗證、agent 評估樣本與資料保留策略；持續檢查狀態機與本機工作佇列的邊界。
- 正式整合仍需本機 PostgreSQL 或 Docker；目前工具不存在，不會為此安裝高權限系統服務。不能把 offline SQL 編譯當成 PostgreSQL 實際整合測試。
- 不宣稱已執行五小時；目標保持 active，後續依計畫繼續本機開發與驗證。

## 接續成果

- 新增 `STEALTH_AGENT_DESIGN.md`：agent 權限、provider 分工、交易鎖策略、評估集與保留策略。
- 新增 PostgreSQL building blocks：每個本人訊號至多 60 筆候選，SQL 先過濾 opt-in、雙向封鎖、分類／地點／時間，再 LIMIT；成團前鎖定並重驗證成員、agent、訊號。未接 HTTP 或實際 DB。
- 修正候選截斷：無關分類／停用使用者不會把真正相容訊號擠出搜尋範圍。
- 新增形成後的 formation 讀取 API，只允許正式參與者取得共享拼單／聊天室 ID。
- 本機 jobs 增加 next_attempt_at，延後安靜／冷卻重試；已無有效訊號時只移除工作，不刪除訊號內容。
- 待做：獨立狀態機檢視、repository 一致性測試、契約漂移檢查、補齊正式整合所需的剩餘 adapter。外部 DB／Apple／模型整合仍不在本輪自動連線範圍。

## 第二階段成果

- 拆開 AgentInput.expected_version 與 agent state；只接受明確 boolean opt-in／acknowledgement。
- agent 一般 version 與 policy_version 分離。新增 tone/avatar_key 與個人邀請文案、why_me；外觀編輯不撤銷同意，配對政策變更則撤銷。
- 新增本人確認的 acceptable_categories：彈性晚餐需求可與明確披薩資源配對，不會擅自擴張飲食接受範圍。
- 新增私有拒絕原因 enum，單次拒絕不自動改偏好；新增 local block/unblock API，不復活失效邀請。
- PostgreSQL/local row→domain 一致性測試、固定 migration 完整欄位比對、全部七張新增表的 RLS 比對。
- migration 審查抓到工作表漏加 RLS 及一個多餘參數，已修正。未曾套用到 DB，因此沒有發生對外資料暴露。
- 新增可選 OllamaDraftProvider：預設停用，固定 loopback、拒絕 cloud model/redirect/proxy/tool calls、schema／大小／逾時／併發限制；只用 MockTransport 測試。確認本機沒有 Ollama/PostgreSQL/Docker 可執行檔，未安裝或啟動它們。
- 測試全域禁止真實 HTTP transport，防止未來錯誤設定誤花額度。只查閱官方協定文件，沒有向模型／資料庫送真實資料。
- 修正 OpenAPI 匯出會修改 app cache 的問題；新增契約與實際 schema 一致性測試。
- uv.lock 保留原有依賴版本，移除新版 uv 的無關 upload-time 格式噪音；offline lock --check 通過，只有 tzdata 新依賴與必要格式版本變更。
- 下一步：補齊 PostgreSQL 寫入／交易 adapter 的本機可驗證部分、狀態機與公平性檢視、評估報告。真實 DB、Apple 與模型效果仍未驗證，不列為已完成。
- 第二階段檢查時間：2026-10-15 17:43 Asia/Taipei（UTC 08:43），開始後約 59 分鐘。完整 pytest 123 passed；Ruff 與 git diff --check 通過。契約目前 11 個 path、15 個 operation。
