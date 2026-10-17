# 偷揪本機開發與驗證

這是新增、預設關閉的本機原型；既有發文等模組保留。尚未接 Supabase JWT、PostgreSQL repository、真實 LLM、Apple 原生模組或推播。正式 schema 有 7 張偷揪表；實際 API 此時使用獨立 SQLite adapter。

## 不需要憑證的驗證

在後端專案目錄執行：

```powershell
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m app.stealth.cli demo
```

`demo` 只使用臨時資料夾中的合成資料，不開網路 port、不呼叫外部服務，不讀 `.env` 的雲端金鑰。完成後由 Python 清理自己建立的暫存目錄。輸出包括 provider、提案人數、理由、待確認項、本機成立狀態與聊天室人數；沒有 token 或他人原文。

示範流程：一人需要披薩、一人願意提供優惠、一人願意順路取餐 → 三方提案 → 全員同意 → 主揪確認費用與分工 → 一筆本機拼單＋三人聊天室。優惠與價格並未由商家驗證。

## 選用：在 loopback 測試 API

先明確建立三個合成測試身份：

```powershell
.venv/Scripts/python.exe -m app.stealth.cli init-local-users
```

這會在 gitignored `.local/stealth-dev-users.json` 保存本機測試 token，在 `.local/stealth.sqlite3` 保存 hash。指令不把 token 印到終端，重複執行不覆寫既有憑證。這些不是 Supabase 帳號，勿放真實個資或將 token 貼入聊天／Git。

在同一個 PowerShell session 啟動：

```powershell
$env:STEALTH_LOCAL_ENABLED = 'true'
$env:ENVIRONMENT = 'local'
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

API 文件位於 `http://127.0.0.1:8000/docs`。本機 adapter 拒絕 Origin header，因此暫不允許瀏覽器跨來源串接，保留 CLI／TestClient 驗證。不要綁定 `0.0.0.0`、開通防火牆、公開 tunnel 或把這個模式部署出去。

### 請求順序

每位使用者使用自己的 `Authorization: Bearer <local-token>`：

1. `PUT /api/v1/stealth/agent`：`enabled=true`；新增使用 `expected_version=0`，更新使用當前 version。
2. `POST /api/v1/stealth/signals`：結構化訊號，必要的 `Idempotency-Key` header 是新 UUID；同一次重試必須沿用 key 與 body。
3. 建立訊號後，API 的本機背景回呼會嘗試配對。訊號與工作紀錄同一交易保存；重啟不會消失，但沒有常駐排程器，須 CLI 重試。
4. `GET /api/v1/stealth/proposals`：每個人查看自己的私有邀請。
5. `POST /proposals/{id}/decision`：`{"decision":"accepted"}` 或 `declined`。
6. 全員同意後狀態為 ready。任一參與者可確認當主揪，呼叫 `POST /proposals/{id}/form`。
7. 訊號被消耗；重疊提案顯示 unavailable。這不會更動原 PostgreSQL 資料表。

本機待配對工作重試（一次最多 20 位，不啟動常駐程序）：

```powershell
.venv/Scripts/python.exe -m app.stealth.cli dispatch
```

人工配對端點 `POST /matches/run` 也保留，僅能為本人啟動。每人至少間隔 30 秒；每位參與者的日邀請配額按其 timezone 計算。預設安靜時段為 22:00–08:00（Asia/Taipei），合成展示可明確設兩個 quiet 欄位為 null。

## 訊號範例

下列時間只是格式範例，實測須換成未來時間，且最晚七天內；每段可用時間不超過 24 小時。

```json
{
  "kind": "need",
  "category": "pizza",
  "title": "今晚想找人分披薩",
  "location_id": "east_gate",
  "starts_at": "2026-10-15T10:30:00Z",
  "ends_at": "2026-10-15T11:30:00Z",
  "expires_at": "2026-10-15T10:00:00Z",
  "budget_max_twd": 150,
  "known_cost_twd": null,
  "tags": ["晚餐"],
  "roles": ["share", "order"],
  "dietary_requirements": [],
  "dietary_verified": []
}
```

- `kind`：need / offer / plan；plan 必須明確選 pickup。
- `category`：pizza / meal / drink / bakery / snack。
- `acceptable_categories`：本人明確接受的替代分類，預設空陣列。例如 meal 可接受 pizza；offer 必須描述實際資源，不可填替代資源。
- `location_id`：east_gate / main_gate / south_gate；首版是合成固定地點，不代表涵蓋全台地理搜尋。
- 金額為整數 TWD，`known_cost_twd` 指每人費用，不能混用總價／優惠面額。
- `roles` 是本人願意做的事，不是對別人的指派。
- 飲食必要限制需要每個餐點來源訊號都有相應聲明，未知不視為安全。這些聲明不是商家驗證或過敏保證。
- 原文只對訊號本人可見。參與者會看到共同地點、時間交集、最低預算上限等聚合資訊，提交前應知情。

Agent 可選 tone=direct/warm/playful、avatar_key=rice/pizza/tea/cat；這些是代號與文案設定，不會下載圖檔。一般 version 用於編輯衝突，policy_version 用於配對授權；改名字／語氣不會讓舊邀請失效。

拒絕可加 `decline_reason`（not_now/time/budget/food/privacy/other），只對本人回傳，不會自動學成永久偏好。接受時不得填拒絕原因。

本機 `POST /api/v1/stealth/blocks/{user_id}` 與 DELETE 可測封鎖／解除；對不存在的 UUID 不揭露使用者是否存在。這是 local_blocks adapter，尚未串入正式 users 封鎖 API。封鎖會使待處理提案失效；解除不復活舊同意。

## 成團確認範例

```json
{
  "content": "三人披薩團，費用與取餐分工已確認。",
  "deadline_at": "2026-10-15T09:50:00Z",
  "confirmed_cost_twd": 130,
  "confirmed_roles": {"order": true, "pickup": true},
  "host_roles": ["order"],
  "acknowledge_unverified_details": true
}
```

截止時間必須在現在之後、不晚於邀請到期與取餐區間結束。已知價格不能改；未知價格只能在所有已提供預算上限內確認。接受提案目前代表同意這個條件範圍，不是已驗證商家報價。若產品要求每人確認最終精確金額，需增加 terms revision＋再次同意流程，不能宣稱此原型已做到。

`host_roles` 只表示這次確認者本人願意承擔的角色。必要角色須已有人在訊號中授權，或由主揪自己補上。其他人的身份與角色對應，在形成共享聊天室之前不揭露。本機成團回應包含 `storage=local_sqlite`，避免混淆為已接正式拼單。

## 已有防護與尚未完成

已有：物件擁有者檢查、私有提案回應、agent opt-in、訊號撤銷、版本失效、期限重驗證、雙向封鎖過濾、邀請限額、signal idempotency、成團唯一性、並發消耗訊號、固定 SQL migration、偷揪表 RLS 預設拒絕。

尚未完成：正式 JWT、正式 PostgreSQL 交易 adapter、直接登入／封鎖 UI、全平台有界 SQL 候選查詢、可靠常駐 dispatch、退出／名額釋放、群組／私訊讀寫 API、真實語意模型與效果評估、Apple 整合、TestFlight、推播。既有功能在這次之前也多為空路由，本次沒有假稱已完成。

SQLite adapter 會掃描合成資料表再過濾；候選組合數有界，但儲存層還不是大規模查詢方案。正式整合必須改成有索引、分頁與權限範圍的 SQL 查詢。

## 可選的本機 Ollama adapter（未安裝模型、未實機驗證）

已提供 adapter 與 MockTransport 測試，不會自動下載模型、啟動 daemon 或連到雲端。當前電腦沒有找到 Ollama 執行檔，因此仍使用預設 offline_rules。

後續若你已自行安裝模型，可使用 `STEALTH_AI_PROVIDER=ollama_local`、指定 `STEALTH_OLLAMA_MODEL`，並在確認 Ollama server 使用 `OLLAMA_NO_CLOUD=1` 啟動後，才設定 `STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED=true`。這是操作前提，backend 不能替另一個本機程序保證雲端設定。

adapter 固定連 `http://127.0.0.1:11434`，停用 proxy／redirect；先查 `/api/tags`，只接受已安裝且沒有 remote_host／remote_model 的 GGUF 模型。未安裝模型回 503，不自動 pull。輸出有 schema、字數、64 KiB 回應上限與逾時限制，不提供工具呼叫；失敗不會偷偷改用 OpenAI。每個 API process 最多一筆同時推論，忙碌直接回安全錯誤；daemon 的執行與排隊仍需另外設定，client timeout 不代表已驗證 daemon 一定停止計算。

本機 LLM 也可能理解錯誤；`is_llm=true` 只表示走了模型 adapter，不代表事實已驗證。草稿仍要本人確認才可變成訊號，不能直接授權取餐或加入。

協定依據：[Ollama Chat API](https://docs.ollama.com/api/chat)、[Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)、[停用雲端功能](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)。

## Migration 驗證

測試會用 Alembic offline mode 產生 SQL 並檢查 schema，不建立連線。`0001_initial` 固定既有 14 張表；`0002_stealth` 新增 7 張表並啟用 RLS。沒有執行 upgrade／downgrade 到任何真實 DB。

開發用 `scripts/freeze_initial_migrations.py` 是一次性凍結工具，拒絕覆寫現有 revision；後續額外手動檢查過的 RLS／約束以 revision 內容為準，不能重新生成覆蓋。
