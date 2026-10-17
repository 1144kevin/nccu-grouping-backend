# 偷揪：新增後端模組規格

狀態：本機開發；不取代發文、拼單、聊天、通知等既有功能。不修改前端。
工作時段：2026-10-15 16:44–21:44（Asia/Taipei），五小時。實際成果與限制記錄於 `STEALTH_PROGRESS.md`。

## 目標與第一版範圍

使用者授權 agent 留意三種短期生活訊號：想要（need）、擁有（offer）、即將做（plan）。後端尋找共同需求與互補資源，建立最多三人的私有提案。所有參與者明確接受、主揪明確確認後才建立既有 Post 與 Conversation；不得自動承諾取餐、付款或加入。

首版只處理吃喝與取餐：固定地點 ID、UTC 時間區間、整數新台幣預算、分類／標籤、飲食限制、可貢獻角色。模型理解文字，程式驗證限制；無法確認的優惠、價格與飲食相容性一律標示待確認。

## 五小時工作計畫

| 時段 | 工作與驗收 |
| --- | --- |
| 16:44–17:44 | 規格、資料契約、資料模型、本機儲存；既有模型與功能保留 |
| 17:44–18:44 | 純函式配對引擎：不同使用者、時間交集、固定地點、預算、資源互補、過期與封鎖；測試反例 |
| 18:44–19:44 | API、agent 授權、本機身份、私有訊號、邀請接受／拒絕與競態處理 |
| 19:44–20:44 | AI provider 介面與離線測試 provider、提案解釋、成團確認與本機示範 |
| 20:44–21:44 | 整合／隱私／並發測試、修復、API 文件、兩週順序與交付紀錄 |

時間是工作分配，允許依測試結果調整；不為填滿時段重複測試，不謊報實際執行時間。未完成項目保留為明確待辦。

## 資料庫設計

原有 profiles/posts/join_requests/conversations 等維持；新增：

| 表 | 主要欄位與規則 |
| --- | --- |
| stealth_agents | user_id 唯一、name、enabled（預設 false）、daily_invite_limit、quiet hours、timezone、version、policy_version；tone/avatar_key 存於 settings；偏好不等於自動加入授權 |
| stealth_signals | owner_id、kind、category、title、location_id、starts_at、ends_at、expires_at、budget_max_twd、tags、dietary_requirements、dietary_verified、roles、status、version；一個訊號只代表本人一席 |
| stealth_proposals | fingerprint 唯一、status、location_id、starts_at、ends_at、expires_at、score、reasons、unknowns、created_at；不可對外宣稱成功率 |
| stealth_proposal_members | proposal_id+user_id 唯一、signal_id、signal_version、agent_version、decision、decided_at；原始訊號只對本人可見 |
| stealth_events | actor_id、proposal_id、event_type、created_at；不保存原始聊天／prompt／秘密 |
| stealth_formations | proposal_id 唯一、host_id、post_id、conversation_id；交易內一次建立，重送不重複 |
| stealth_match_jobs | actor_id 唯一、generation、requested_at、next_attempt_at；訊號與待配對工作一併保存，避免重啟丟失觸發 |

正式 PostgreSQL 存取透過 repository；這次以獨立 SQLite 本機 adapter 驗證流程，不操作外部 DB。SQLite 不能冒充已驗證的 PostgreSQL／Supabase 整合。正式 migration 必須固定 schema，不以未來 ORM metadata 動態建表。

## API 契約（/api/v1/stealth）

| Method | Path | 用途 |
| --- | --- | --- |
| GET / PUT | /agent | 取得／取代自己的 agent 設定；版本控制，關閉後撤銷尚未成立提案 |
| POST / DELETE | /blocks/{user_id} | 本機封鎖／解除；只影響本人的關係，封鎖使相關待處理提案失效，解除不復活舊提案 |
| POST / GET | /signals | 建立經本人確認的結構化訊號／列出自己的訊號（cursor pagination） |
| GET / DELETE | /signals/{id} | 本人讀取／撤銷；撤銷會使引用它的待處理提案失效 |
| POST | /drafts | 離線 provider 從文字產生草稿，明確回傳 provider 與待確認欄位；不會自動保存訊號 |
| POST | /matches/run | 為本人執行有界配對，限制候選量／輸出數／頻率，其他參與者須啟用 agent |
| GET | /proposals | 只列出本人收到的提案（cursor pagination） |
| GET | /proposals/{id} | 只回傳團體的聚合條件、待確認項與本人的參與狀態，不外洩別人的原文、身份或訊號 ID |
| POST | /proposals/{id}/decision | accepted / declined；全員同意後狀態為 ready，尚不自動成團 |
| POST | /proposals/{id}/form | 全員已同意＋主揪確認正式內容／截止時間後成立；交易、版本與過期檢查、重送安全 |
| GET | /proposals/{id}/formation | 正式成立後，僅參與者取得共享拼單／聊天室 ID |

無權限的物件統一 404；未登入 401；衝突 409；格式 422；用量限制 429；未啟用本機功能／正式整合未完成 503。時間必須帶時區，輸出 UTC ISO 8601。日配額採 agent timezone；配對端點不是公開全平台資料入口。

## 配對與 agent 邏輯

1. 明確 opt-in。沒有啟用 agent 的人不可成為候選；訊號預設限時且不是公開貼文。
2. 過濾已撤銷、過期、時間已結束、被封鎖、同一使用者重複與安靜時段。
3. 按固定地點、本人確認的分類／acceptable_categories、時間重疊取有界候選；相同地點不代表整段行程公開。
4. need 是組合錨點：need+need（共同需求）、need+offer、need+plan、need+offer+plan。禁止純 offer+plan 自動臆造需求。
5. 必要條件：所有人的時間交集非空，已知固定價格不超出任何參與者預算；飲食必要限制不能只憑未標示判斷安全。
6. 用可解釋啟發式分數排序（非機率）：共同標籤、時間交集、互補角色、資訊完整度；穩定 tie-break，最多三人。
7. 每份提案保留來源版本與 fingerprint；agent 的 matching policy_version 用於授權快照，外觀／名字／語氣只更新一般 version。相同輸入不重複邀請，被拒絕不反覆重建同一提案。
8. 每次決策／成團重驗證 opt-in、期限、版本、封鎖。已被另一提案使用的訊號不可再成團。邀請不是預留名額。
9. 拒絕使整份提案終止，其他人只見「方案已失效」，不揭露誰拒絕。安靜時段只禁止新邀請，不禁止本人讀取／處理。
10. agent 是偏好、訊號、授權與 provider 的組合，不是假裝大量模型互聊；日配額、冷卻時間與狀態機由程式執行。

## AI 分工與真實性

Apple：手機上的欄位抽取、潤稿、個人偏好整理，使用者確認後傳送結構化訊號。這次不改前端，也不宣稱完成 Apple 整合。

後端：跨人候選檢索、互補組合、約束驗證、理由與未知欄位。本機 deterministic provider 只能作規則草稿與可測說明，必須標示 provider，不稱已接通 LLM。未來 embedding／OpenAI provider 先經介面、輸出 schema、逾時、預算與脫敏設計再加入。本次禁止連外與付費呼叫。

## 本機安全邊界

- 功能預設關閉；本機 adapter 只允許 local/test，認證 fail closed；沒有無認證「假 user id」公開入口。
- 本機測試憑證與 SQLite 放 `.local/` 並 gitignore；DB 僅保存 token hash；不用真實個資，不輸出 token 至工具紀錄。
- 本機伺服器僅綁定 127.0.0.1；不能當成正式 JWT、RLS、rate limiting 或 deployment 的替代品。
- 不部署、不 push、不套用外部 migration、不讀秘密、不刪既有資料、不改作業系統／防火牆／驅動。
- 配對過程可使用已授權私有訊號，但回應只顯示聚合的合作條件。任何模型輸出都不得直接執行工具、SQL 或更改權限。

## 兩週開發順序（10/15–10/29）

| 日期 | 工作 |
| --- | --- |
| 10/15–16 | 本機偷揪閉環、契約與測試；固定一期範圍 |
| 10/17–18 | PostgreSQL repository、固定 migrations、本機 PostgreSQL 整合測試；Supabase JWT 與 profiles 初始化 |
| 10/19–20 | 補齊原有貼文／申請／退出交易；偷揪轉 Post／Conversation 的正式交易 |
| 10/21 | agent、訊號、提案 API 契約凍結，提供前端串接範例與測試資料 |
| 10/22–23 | Mac 上 Apple 原生模組驗證；前端由原團隊／使用者串接（本次不修改） |
| 10/24–25 | 群組／私訊、站內通知與封鎖；競態、撤銷、過期、身份隔離測試 |
| 10/26 | 用合成需求建立評估集，比較關鍵字、規則與語意候選；接限額模型須另有憑證與授權 |
| 10/27 | 設備回歸、競賽示範與 TestFlight build；外部測試審核預留時間 |
| 10/28–29 | 修復與緩衝；推播與常駐 worker 若影響時程移至第二版 |

上線與 TestFlight 是後續交付，不在這次本機安全工作範圍；Apple 審核與外部服務可用性不可保證。測試數據需真實量測，不宣稱尚未驗證的成團率提升。
