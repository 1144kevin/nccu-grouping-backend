# 原本登入與核心拼單：本機驗證

這是本機開發版本，不是正式 Supabase Auth 或 TestFlight 後端。只監聽 loopback，使用 `.local/core.sqlite3`，不連 Supabase、不寄信、不呼叫 AI、不部署。MCP 的 PAT 完全不參與 APP 登入，也不能放進前端。

## 啟動（這台 Windows 已有依賴）

開兩個 CMD 視窗，分別保持開啟。若 8000／8081 已被先前測試占用，先在原本服務視窗按 Ctrl+C；不要任意終止其他程序。

後端：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\run_local_core.py
```

前端：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-app
npm run dev:local
```

瀏覽器開啟 `http://localhost:8081`。前端指令以 process environment 啟用本機模式，無須修改現有 `.env`；Expo offline 由 `EXPO_OFFLINE=1` 啟用，`--localhost` 讓 Metro 只監聽 loopback。這版不開 LAN 或 tunnel，因此實體 iPhone 不能直接連 Windows 的 localhost。

原本展示介面仍保留：使用 `npm start`，並確保 `EXPO_PUBLIC_CORE_API_ENABLED` 沒有設為 `true`。本機 API 模式不會在後端失敗時偷偷改用假資料。

## 手動走完主揪與團員流程

1. 一般瀏覽器建立 `host@example.test`，名稱「測試主揪」，使用專用測試密碼（10～128 字）。這不是 Supabase 控制台帳號，不會寄驗證信。
2. 建立文字拼單：內容至少 10 字、需求人數 2 人（含主揪）、金額可留空或填 85.50、截止選未來時間。按「使用政大正門測試地點」即可完全不依賴外部地點服務。
3. 發布後首頁顯示 1/2 人。重新整理，拼單仍存在。
4. 使用無痕視窗建立 `member@example.test`，名稱「測試團員」。按「想被揪」。畫面應顯示「取消申請／待主揪接受」，仍為 1/2 人。
5. 主揪開「拼單 → 我主揪的 → 該筆拼單」，接受申請。畫面應變為 2/2 人、已額滿。可下拉重新整理；開啟中的畫面也會每 10 秒重新讀取。
6. 團員按「退出並釋出名額」，應回到 1/2 人；「申請／已加入」清單移除該筆，主揪清單保留已退出紀錄。
7. 團員可重新申請；主揪必須重新接受。主揪可以拒絕待處理申請，或經確認取消整筆拼單。
8. 登出再登入，原本發布及參與狀態仍在。未登入不可呼叫拼單 API。Web token 使用每頁籤 sessionStorage；手機版儲存介面使用 SecureStore。
9. 個人檔案 → 編輯個人資料，可修改名稱、介紹與頭像圖示。重新整理後應保留，原本發布的拼單也顯示更新後的名稱與圖示。
10. 主揪在尚無待處理申請或已加入團員時，可從詳情「編輯拼單」修改內容、標籤、人數、金額、截止與地點。有人申請後，編輯入口應消失；直接呼叫 API 也會拒絕。全部申請被拒絕或取消、團員退出後，才可再修改。

測試帳號只用虛構資料，不要沿用任何真實服務密碼。Web 同一個頁籤只保留一個帳號，跨帳號示範建議一般／無痕視窗。

## 規則與界線

- 主揪自動占一位；申請不占位，接受才占位。SQLite `BEGIN IMMEDIATE` 將接受／退出操作放在同一交易，避免最後一個名額被重複接受。
- 截止時間使用帶時區 ISO 8601，後端統一 UTC；畫面使用裝置時區。截止後不能新申請、接受或退出；取消狀態不能加入。
- 只有主揪可讀申請清單、接受／拒絕與取消拼單。一般使用者只能看到自己的參與狀態，API 不接受冒用的 author_id／requester_id。
- 重複送出加入、接受與退出不會重複占位或釋出。發布使用 Idempotency-Key；同一內容重試保留 key，內容改變用新 key。
- 個人資料與拼單修改都需提交 `expected_version`，拒絕覆蓋別處已更新的內容。申請加入也必須附目前看到的拼單版本；舊畫面不能直接加入已改條件的拼單。修改與申請同時送出時，只允許其中一方成功。
- 密碼採 PBKDF2-SHA256、獨立隨機 salt，session token 只存 SHA256 hash；session 有效 24 小時，同帳號最多五個有效 session。登出撤銷目前 session。
- 本機 adapter 預設關閉，環境不是 local 就拒絕操作；禁止代理轉送標頭與非允許的瀏覽器 Origin。這些是本機防護，不代表已完成正式環境身份驗證。
- 聊天、通知、其他展示功能保留；個人頁不把原本 Lv.5／98 分的展示資料當成新註冊者的真實等級。圖片、錄音、寄信、忘記密碼、正式 RLS、Supabase/JWKS 驗證與推播仍待整合。
- 偷揪獨立模組及 `.local/stealth.sqlite3` 保留；本輪核心帳號尚未與偷揪 synthetic token 合併，不假裝已串通。

## API

所有路徑前綴 `/api/v1`，除了 register/login 都需要 Bearer token。

前端協作者可查看 `docs/core.openapi.json` 的完整輸入／輸出型別。`GET /posts` 另支援 `q`（內容、標籤、主揪名稱）與 `active_only=true`（尚未截止且未取消）；搜尋也保留分頁，不混入展示資料。

| 方法 | 路徑 | 用途 |
|---|---|---|
| POST | `/auth/register` | 建立本機測試帳號並登入 |
| POST | `/auth/login` | 信箱／密碼登入 |
| GET | `/auth/me` | 讀本人資料 |
| PATCH | `/auth/me` | 修改名稱／介紹／圖示，附 expected_version |
| POST | `/auth/logout` | 撤銷本次 session |
| GET | `/posts?scope=feed\|hosted\|participating` | 分頁拼單清單 |
| POST | `/posts` | 發布，必須附 Idempotency-Key |
| GET | `/posts/{id}` | 拼單詳情與本人狀態 |
| PATCH | `/posts/{id}` | 主揪修改，附完整 PostInput 與 expected_version |
| POST | `/posts/{id}/join-requests` | 送出加入申請，body 為 `{"expected_version":目前版本}` |
| GET | `/posts/{id}/join-requests` | 主揪讀取申請 |
| PATCH | `/join-requests/{id}` | 主揪接受／拒絕 |
| DELETE | `/posts/{id}/membership` | 取消本人申請或退出 |
| POST | `/posts/{id}/cancel` | 主揪取消拼單 |

## 自動驗證

不開伺服器也能跑完整雙人 API 流程（隔離暫存 DB，結束自動清除）：

```cmd
.venv\Scripts\python.exe scripts\smoke_core.py
```

後端（pytest 暫存放在專案內；fresh basetemp 名稱可按每次執行自行調整）：

```cmd
.venv\Scripts\python.exe -m pytest -o addopts= -q --basetemp=.local/manual-pytest --cache-clear -o cache_dir=.local/pytest-cache
.venv\Scripts\python.exe -m ruff check app tests scripts
```

前端：

```cmd
npm run typecheck
npm test -- --runInBand --cacheDirectory .expo/jest-cache
```

測試使用隔離 SQLite／mock HTTP，不需要 Token 或付費服務。實際瀏覽器測試另使用 `.local/core-ui-smoke.sqlite3`，不會混入預設開發資料庫；沒有更動雲端 Supabase。

本輪已驗證：後端 158 項測試、前端型別檢查與測試；雙帳號瀏覽器走完發布 → 待接受 → 額滿 → 退出釋出名額 → 取消，也驗證登出保護路由、過期登入、搜尋、個人資料保存及主揪編輯。iOS 僅做 JavaScript 匯出檢查，尚未用 Mac 原生建置或實機驗證；這版不表示已可送 TestFlight。

## 後續接正式服務

維持同一組拼單 API 契約，將本機身份替換為 Supabase Auth JWT 驗證、SQLite 替換為 PostgreSQL transaction／row lock，再驗證 RLS、驗證信與手機連線。不得把本機密碼 hash 或測試 token 視為 Supabase session；不得把 MCP PAT／service_role 放進 APP。
