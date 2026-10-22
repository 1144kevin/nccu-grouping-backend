# 一句話偷揪：Windows／Mac 交接

新版主流程：一句話 → 分身整理與找人 → 初步配對與分工建議 → 全員同意聊聊 → 私人協調聊天室 → 談好後才確認正式拼單。

## 目前狀態

2026-10-22：前後端程式、隔離元件測試及無網路 Docker PostgreSQL 演練已完成。自動審核最初拒絕共享結構變更後，本人明確同意套用到指定開發專案；已套用 `core_stealth_quick_coordination` migration，並通過 Supabase 交易內隔離帳號測試，所有測試資料回滾。沒有部署 App／API，不付費、不 push。

驗證紀錄：前端50組／217項測試、76項相關後端測試、TypeScript、變更檔 ESLint／Ruff、iOS JS 套件編譯通過。隔離 PostgreSQL 與 Supabase 的同意／訊息／品牌／撤回／過期／封鎖／查表權限測試通過並完整回滾。Qwen3.5:4b 真實本機推論將「我今天想喝50嵐」整理為飲料、品牌50嵐、預設正門／兩小時，價格及預算保持未知，未替本人承諾下單或取餐。iPhone 的新版雙人全流程仍須本人驗收。

Web 已可使用新畫面；`GET /api/v1/stealth-cloud/quick-status` 登入後只讀查核必要 RPC，若搬機漏套 migration，會停用新版送出並保留「分身與需求進階設定」，不自動執行 DDL。舊登入、拼單、聊天室、訪談與完整方案確認保留。

## 使用者看見什麼

1. 輸入「我今天想喝50嵐」，按「幫我揪」。首次按鈕會明確寫「啟用分身並幫我揪」，不需要先捏完分身或填整張表。
2. 本機模型理解文字；後端從**本人近30天最近的同類需求**沿用地點與預算。不沿用舊價格、取餐責任、優惠券或飲食安全保證。沒有紀錄時暫用政大正門、接下來兩小時，畫面會說明，可直接在句子中改地點與時間。
3. 同一次送出自動做向量搜尋、候選排序與判讀。不在每十秒輪詢中重跑模型。失敗保留已存的需求與規則配對，沒有改用付費服務；「請分身再看看」統一重試。
4. 卡片只顯示適合原因與建議分工。優先用明確志願者，沒有志願者時暫建議成員1下單、成員2取餐，**只是可交換的建議**。
5. 點「同意初步安排，進聊天室」只提交參與意願；所有人均同意才可讀寫私人協調訊息。實際價格、品項與責任可在聊天室談，不需捏造零元或先填正式方案。
6. 談好後點「談好了，確認正式拼單」，沿用原版費用與分工同版全員確認；改版清空舊同意。正式成團前不新增公開貼文或執行付款。

## 不是什麼

目前以既有本機模型＋共用協調器＋規則草案實現分身代理流程，並非多個 LLM 無限制互傳私人資料、長期自我學習或自動購物。習慣只取近期同類紀錄，不宣稱已建立完整使用者人格。Mac 的 Apple 裝置訪談仍保留在進階介面，沒有在 Windows 假裝完成原生整合。

已知品牌約束第一版支援 50嵐／五十嵐、清心、可不可、迷客夏的明確標籤；不同品牌不會因都是飲料而直接配對。未辨識的品類請補店名或餐點類型；非餐飲情境仍留後續擴充。

## 已經本人核准套用的雲端變更

僅限 `nccu-grouping-dev`（`axdwbfhrkbovgrbngrrh`）的 `supabase/core_stealth_quick.sql`：

- 新增 `core_stealth_rooms` 與 `core_stealth_room_messages`，啟用 RLS 並禁止匿名／登入者直接查表。
- 新增使用目前登入者 session 的 `core_stealth_quick` RPC：本人需求重試查找、全員同意後開聊天室、查最近100則訊息、冪等傳送。
- 包裝既有配對函式以增加品牌限制，保留原本封鎖、測試隔離、預算、地點、時間與每日上限檢查。
- 包裝配對顯示函式，回傳 `coordination_opened`，撤回或過期後仍能找到唯讀紀錄。
- 帳號刪除沿用原本提案刪除及 FK cascade 清理私人聊天室，訊息發送者亦有刪除 cascade；原正式聊天室不改動。

不能重跑已套用 migration。此次已先查 migration 歷史，透過 apply_migration 套用一次，再執行 `core_stealth_quick_smoke.sql` 的交易回滾測試；不保留測試 Auth 帳號、訊息或配對。**不是部署 API 或 App**，不啟用付費服務、不 push。

## 本機驗證

在後端資料夾：

```powershell
$env:STEALTH_AI_PROVIDER='offline_rules'
$env:STEALTH_SEMANTIC_ENABLED='false'
.venv\Scripts\python.exe -m pytest tests/test_quick_stealth.py tests/test_quick_stealth_api.py tests/test_stealth_cloud.py tests/test_device_ai.py tests/test_stealth_planner.py -q -p no:cacheprovider
.venv\Scripts\python.exe -m scripts.rehearse_quick_stealth
```

第二個指令只建暫時、無網路、無主機掛載的本機 PostgreSQL 容器，跑完移除；不輸入 Supabase 備份或真實憑證。測到兩人同意、陌生人拒絕、品牌隔離、本人重試查找、訊息冪等、撤回唯讀、封鎖與禁直接查表；既有正式成團仍由舊測試保護。

在 App 資料夾：`npm run typecheck`、`npm test -- --runInBand`。新案例在 `src/components/quick-stealth.test.tsx`；包括未套用 migration 時保留舊設定入口。

## 接通後的雙帳號驗收

1. iPhone A 與電腦 B 使用不同測試帳號，兩邊都說「我今天想喝50嵐」，按各自的「幫我揪」。首次啟用須本人點擊；不由 Codex 替真人按。
2. 不填手動表單，等結果更新。先檢查地點／時間建議與本人需求；兩邊窗口必須有交集，分身不能在安靜時段或超過當日邀請上限。
3. A 同意後只能等待；B 同意後可進協調聊天室。A 的結果頁更新後同樣能進入。
4. 聊天討論品項與取餐，斷線重試不重複送出。撤回／邀請過期後唯讀，封鎖後不再讀寫。
5. 確認實際費用後才開正式拼單方案，依原驗收清單驗證改價重新同意及成團聊天室。

私人協調室目前顯示最近100則，入口在偷揪頁，尚未合併一般聊天室收件匣／未讀計數／推播。原邀請有效期未延長，過期會唯讀。這些限制不應隱藏或宣稱已正式營運完成。
