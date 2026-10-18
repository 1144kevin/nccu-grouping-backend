# 本機程式 + Supabase 開發專案

前端與 FastAPI 仍只在這台 Windows 的 localhost 執行。帳號使用 Supabase Auth，拼單資料使用開發專案 `axdwbfhrkbovgrbngrrh` 的 PostgreSQL；沒有部署，也沒有啟用付費 AI、SMTP 或其他付費服務。

Google 登入與本機回呼已加入，使用者已確認 Google 真實登入成功。設定參考見 [Google Cloud／OAuth 設定](GOOGLE_LOGIN_SETUP.md)。

## 本輪新增：原 App 雲端功能

- 發布頁可選一張 JPEG／PNG 附圖。圖片經後端驗證、縮小、移除 EXIF 後，與拼單在同一資料庫交易發布；失敗保留草稿，可直接重試且不重複建單。首頁與詳情重載後仍讀取雲端圖片。詳見下方「拼單附圖」。
- 「帳號與隱私」可儲存個人通知偏好，使用版本號避免覆蓋另一個頁籤的修改。聊天／申請開關控制 App 內徽章；不隱藏申請紀錄，也不宣稱已啟用手機推播。
- 「推薦偏好」可儲存最多24個餐點／地點標籤；首頁「依我的偏好」以明確標籤與地點比對排序仍進行中的拼單。這是規則排序，沒有冒充 AI 或保證成團。原本最新清單與搜尋保留。
- 聊天室可個人封存、移回收件匣、靜音提示。這些是本人帳號設定，不刪訊息、不退出拼單、不影響其他人。封存／靜音群組不計入自己的未讀提示。
- 首頁「沒興趣」與「截止提醒」改存本人雲端帳號，切換帳號不共用。截止提醒可在「拼單動態 → 我的截止提醒」查看，以資料庫時間判斷截止前30分鐘；只有 App 前景頁面更新，沒有鎖屏通知或背景 worker。
- 幫助頁可實際送出問題回報、查自己的近期回報；貼文、公開個人資料與有權讀取的聊天訊息可檢舉。回報私下存入資料庫，只回覆「已收到，待查看」，不自動停權、刪內容，也尚未建立管理者處理介面。同內容重試沿用 key；每人每小時最多5筆。
- 搜尋可切換貼文、公開使用者、自己的聊天室訊息、自己的已結束紀錄。後三類需2～80字、手動按搜尋；信箱不在搜尋範圍，私人訊息必須符合目前聊天室成員權限。退出後立即失去該群組的訊息搜尋權限。
- App 進入背景／inactive 時暫停頁面輪詢與已讀提交；iPhone Google 原生回呼程式已加入但預設未啟用。詳見 [iPhone／TestFlight 準備](IPHONE_TESTFLIGHT.md)。

新資料表：`core_account_preferences`、`core_inbox_preferences`、`core_saved_post_preferences`、`core_reports`。全部啟用 RLS 並禁止 client 直接存取；透過驗證當前 Auth session 的 RPC 操作，不使用 service-role key。`core_search` 與 `core_recommendation_feed` 只讀取符合範圍的資料。

本機 AI 新增原句依據 `evidence`，可在 App 展開查看預算、每人費用、相對時間與角色來源。金額核對區分預算、整團總價、每人費用與人數；沒有相符來源就清空補問，不會四捨五入金額。免費與未知費用分開，明確接受的替代餐點會帶入表單。拒絕取餐不代表已回答所有角色問題；模型分類仍可能錯，必須人工檢查。

固定合成評估集位於 `tests/fixtures/stealth_intake_eval.json`，可執行 `.venv\Scripts\python.exe scripts\evaluate_local_intake.py`，只把固定假資料送到本機 Ollama，不連 Supabase。20組真實本機推論通過19組，當時中位數21.12秒（含其他本機工作負載）；最新核對規則離線重播同一批結果仍為19組通過。尚未通過的 `explicit_pickup_plan` 會漏掉分類／取餐行程，需補充或手動填寫。這不是正式準確率或效能承諾，沒有隱藏失敗案例。完整合成結果留在忽略於 Git 的 `.local/evals/`。

另新增 `tests/fixtures/stealth_intake_challenge.json` 十二組獨立反例，以 `--suite challenge` 執行。第一輪真實推論11/12，找到「希望有人願意取餐」誤判本人志願；後端新增完整原句上下文核對，包含引句外的要求別人／疑問／假設，清空模糊角色並補問。另明確拒絕未標示台幣的外幣金額，不自行換匯。相同提示／模型下重新真實推論12/12通過，中位數17.33秒；標準集舊輸出重播仍19/20。兩組是有限合成案例，不合併宣稱一般準確率。這些核對不取代語義理解或全員親自同意，分類／原句仍可能漏掉。

本輪另實際驗證三個隔離身份：同版三人確認才建立一筆拼單與聊天室，改版清除舊同意，重送不重複；兩份申請同時搶最後名額只接受一人，退出可釋出名額；同 key 的同時發布與聊天只建立一筆；三次同時退出／審核都沒有超收或資料庫錯誤。模型補問與「套用到表單」後，資料庫仍只有原本那三筆隔離需求，未額外寫入。

本輪三個臨時 Auth 身份、sessions／refresh tokens、六筆隔離拼單、私人回報／偏好、需求與配對及本機測試憑證檔已清除。資料庫恢復七筆示範拼單、三則示範訊息、三筆示範偷揪需求、五筆原有 Auth／profile，沒有本輪測試身份。畫面證據留於 `.local/core-evidence/four-hour-*.png`；截圖中的隔離資料已移除。

測試這些功能時，可用自己的 Google 開發帳號從 Drawer 進入各頁。先儲存偏好、重新整理確認仍保留，再換另一帳號查看是否隔離。對示範拼單設定提醒或沒興趣只改自己的設定；不要把測試檢舉當成真實處置結果。這輪自動驗證另用三個隔離假帳號，測後會清除，不使用既有 Google 使用者的 session。

## 啟動

本輪最終驗證：後端334項、前端118項測試通過；Ruff、TypeScript、ESLint、兩項 release 設定測試通過；Web／iOS JavaScript 與資源離線匯出成功。後端有兩項既有依賴 deprecation warnings。前端冷快取第一次有5秒 timeout，暖快取與明確15秒上限完整重跑皆通過；未刪除或跳過斷言。這些不是實機／簽章／TestFlight 測試。

可重跑後端 `.venv\Scripts\python.exe -m pytest -o addopts="" -q --basetemp=.local/pytest-manual-01 -o cache_dir=.local/pytest-cache`（每輪改用新的 basetemp 名稱）；前端 `npm test -- --runInBand --cacheDirectory=.local/jest --testTimeout=15000`、`npm run typecheck`、`npm run lint`、`npm run test:release-config`、`npm run ios:preflight`。SQL smoke 檔僅供指定開發專案的隔離交易驗證，請勿一鍵重跑所有 migration。

開兩個 CMD 視窗，保持開啟。若之前的本機模式還在跑，先在它的服務視窗按 Ctrl+C。

後端：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\run_supabase_core.py
```

前端：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-app
npm run dev:supabase
```

開啟 `http://localhost:8081`。登入頁應顯示「登入 Supabase 開發帳號」，登入後頂端應顯示「Supabase 開發版」。本機 SQLite 帳號與雲端帳號不同，舊的本機密碼不能直接登入雲端；這次沒有搬移 SQLite 測試資料。

後端讀取忽略於 Git 的 `.local/supabase-dev.env`，帳號／拼單只需要這個開發專案的 URL 與 Publishable key。圖片另外讀取 `.local/media-signing.env` 中的私人後端驗證金鑰，目前本機已設定，不能貼到對話、前端或 Git。它只驗證圖片，不具 Supabase 管理權。程式拒絕其他專案 URL、secret/service-role key，以及同時啟用 SQLite 與 Supabase 模式。MCP Token 不參與 App 執行。

## 拼單附圖

1. 發布頁選一張圖片、填寫內容／地點／截止時間，再發布。預覽只在本機；發布才傳到指定開發專案。原本純文字拼單仍可使用。
2. 只接受靜態 JPEG／PNG，原檔最多4 MiB、1600萬像素。後端依 EXIF 方向旋轉，縮到最長邊1600像素，再畫到全新 RGB 畫布，移除 EXIF／GPS／註解／ICC／原檔名，輸出最多2 MiB JPEG。不接收外部圖片網址或檔案路徑。iPhone 選圖的 HEIC 轉換仍須實機驗證；後端不直接解碼 HEIC。
3. `core-post-images` 是私人儲存桶。未發布副本只有本人可讀；發布後所有已登入 App 使用者可讀附圖。短效簽署網址60秒內可供持有連結者讀取，因此請勿轉貼。畫面只使用記憶體圖片快取，不把網址當公開永久連結。
4. 後端讀回實際 Storage 檔案核對 SHA256，以獨立 HMAC 金鑰綁定使用者、圖片、內容 hash 與 Storage object ID，資料庫才接受 ready 狀態。直接上傳 Storage 或偽造 ready 請求不能繞過圖片處理。資料表禁止 client 直接讀寫；Storage 使用當前使用者 JWT 與 RLS，不使用管理金鑰。
5. 開發版限制每帳號20份、整專案100份圖片預留，含已發布與未發布。每檔最多2 MiB，因此邏輯儲存上限約200 MiB；這不是費用保證或正式可擴充配額。達限需清理未發布副本或另規劃已發布媒體生命週期，不自動升級方案。
6. 離開發文頁可能留下未發布雲端副本。到「帳號與隱私 → 未發佈的圖片副本」兩步確認清理，只刪上傳副本，不刪手機原圖。已發布圖片暫不支援更換／移除，也不支援聊天附圖、多圖、語音上傳或 AI 看圖。

已套用 `core_media.sql`，新增 `core_media_assets`、`core_post_images`、私人驗證金鑰表與私人 Storage bucket；既有文字發布 RPC 保留，只擴充貼文讀取的 `image_id`。`core_media_smoke.sql` 在交易中驗證圖片預留重試、每人配額、身份隔離與失效 session，最後 rollback，不建立真正檔案。

另外以兩個隔離合成帳號實測 API 上傳／實際檔案讀回、EXIF 移除、偽造驗證拒絕、未發布存取拒絕、重複發布只建一筆、已發布檔案禁止刪除；瀏覽器也實際選擇合成圖片發布，重整首頁與詳情確認存在。兩個測試帳號、兩筆拼單、所有測試 blobs／預留／session 均已清除；保留原有七筆拼單、三則訊息、三筆偷揪需求、五筆帳號與 profile。證據 `.local/core-evidence/four-hour-image-*.png` 的資料已移除。

## 測試登入與拼單

1. 使用已驗證的 Supabase 開發帳號登入。新的帳號請由頁面註冊，依專案設定完成信箱驗證後再登入。
2. 主揪發布文字拼單，設定 2 人（含主揪）、未來截止時間與測試地點。發布後為 1/2。
3. 用無痕視窗登入另一個開發帳號，送出申請；仍應為 1/2，顯示待主揪接受。
4. 主揪在「拼單 → 我主揪的 → 詳情」接受申請，應為 2/2。團員於截止前退出，應恢復 1/2。
5. 驗證主揪編輯、拒絕、取消、搜尋，以及個人資料修改；有人申請或已加入時不可修改拼單條件。
6. 重新整理或重啟本機 API，已儲存資料仍應存在。登出後需重新登入。

目前 Supabase 設定啟用了 Email 登入，且要求信箱驗證。**沒有關閉驗證設定。** 尚未設定自訂 SMTP；預設寄信服務有限制，可能無法寄給團隊外信箱。開發測試可由專案管理員在 Authentication → Users → Add user 建立專用測試帳號並明確確認該測試帳號；不要使用真實服務的共用密碼。對外測試前另接免費額度的寄信服務。

雲端頁面的註冊必須使用真實可收信信箱，不能填 `kevin@example.test` 等虛構地址。Supabase 會回傳 `email_address_invalid`；這不是後端服務沒有啟動。先用 Supabase 組織成員的真實信箱及另一組 App 測試密碼註冊；若回傳不允許寄信，需處理 SMTP，或先使用管理員建立的已驗證測試帳號。

已加入 refresh token 自動續期；有效登入請求收到 401 才續期，並共用一次續期處理，避免多個請求同時輪替憑證。暫時斷線不清除登入憑證，登出或切換帳號後的舊續期結果不能覆蓋新狀態。SQLite 模式不使用雲端續期。已串接文字聊天室與申請動態；付款、語音上傳與手機推播尚未整合。偷揪新增使用同一套雲端身份的 adapter，原本地端原型繼續保留，兩者不共用 SQLite 資料。

舊版登入只保存 access token；**更新後請自行登出，再用 Google 登入一次**，才會保存完整續期憑證。Web 保存在該頁籤的 sessionStorage，原生端使用 SecureStore；不放在網址、localStorage 或 Git。重新整理可保留登入，關閉頁籤後需重新登入。

## 本輪示範資料與操作

已在指定開發專案實際建立七筆 `showcase_20261017_v1` 示範拼單，前端從資料庫的 `is_demo` 欄位顯示提示，並非寫死假清單。前五筆分配給既有 Google 登入帳號當主揪，另外兩筆讓此帳號練習參與。

| 拼單 | 初始狀態 | 可測試操作 |
| --- | --- | --- |
| 便當晚餐 | 3/5，兩名團員、一名待接受 | 接受小黃後成為 4/5；拒絕、取消 |
| 可頌早餐 | 1/4，尚無申請 | 編輯內容、名額、價格與截止時間 |
| 手搖飲料 | 4/4，額滿 | 查看團員；不能繼續接受新申請 |
| 昨日午餐 | 2/3，已截止 | 查看紀錄；不能再加入或退出 |
| 披薩聚餐 | 1/3，已取消 | 查看取消狀態 |
| 咖啡外帶 | 3/4，你已加入 | 「申請／已加入」中退出，恢復 2/4 |
| 麵包分享 | 1/3，你的申請待接受，金額免費 | 取消申請，再次申請；待接受不占名額 |

開啟首頁，或在「拼單 → 我主揪的／申請與已加入」查看。搜尋「開發示範」可找到這批資料。首頁主揪按鈕「管理拼單」直接開啟詳情，已截止或取消仍可查看紀錄。

示範主揪阿揪與三位示範團員不是可登入的帳號：沒有密碼、OAuth identity 或 session，也不寄信。他們的主揪拼單不會自動接受你的申請。Auth Users 與 core_profiles 看到 `【示範】` 人物屬於預期；這些不是新註冊的真實使用者。

「我的拼單」提供兩步確認的清理入口，只移除分配給目前帳號的示範拼單、申請、聊天訊息與已讀位置。若已有其他真實使用者參與，即使申請已退出或被拒絕也保留，並顯示保留筆數。普通拼單不受影響。清理不刪除任何 Auth 帳號，示範人物與私有批次記錄保留；清理後不會自動重新灌入資料。

`supabase/core_demo_schema.sql` 與 `core_demo_indexes.sql` 已透過 migration 套用；`core_demo_seed.sql`、`core_demo_participation.sql` 是已執行的資料腳本，不是啟動步驟。重跑相同批次不應用來重置使用者的測試結果。`core_demo_smoke.sql` 使用隔離交易驗證跨帳號清理被擋、真實團員紀錄保留與 session 撤銷，最後全部回滾，與留下給你操作的七筆資料分開。

新 API：`POST /api/v1/auth/refresh` 接收 refresh_token；`DELETE /api/v1/development/demo` 需目前使用者 access token，不接受其他帳號 ID。

## 前後端串接索引

以下路徑均以 `/api/v1` 為前綴；除了登入／註冊／Google 交換／續期，皆需要 App 使用者的 Bearer access token。MCP Token 不可拿來呼叫這些路徑。

| 畫面／用途 | API | 主要資料來源 |
| --- | --- | --- |
| 登入與個人資料 | `/auth/login`、`/auth/me`、`/auth/refresh` | Supabase Auth、`core_profiles` |
| 首頁、搜尋、個人拼單與過往紀錄 | `GET /posts?scope=feed\|hosted\|participating\|history` | `core_posts`、`core_join_requests` |
| 發布、編輯、查看 | `POST /posts`、`PATCH /posts/{id}`、`GET /posts/{id}` | `core_posts` |
| 申請、審核、退出、取消 | `/posts/{id}/join-requests`、`PATCH /join-requests/{id}`、`DELETE /posts/{id}/membership`、`POST /posts/{id}/cancel` | `core_join_requests`、`core_posts` |
| 聊天室清單與訊息 | `GET /conversations`、`GET/POST /conversations/{id}/messages` | `core_chat_messages`、目前拼單成員 |
| 待審核／未讀徽章與已讀位置 | `GET /conversations/activity`、`POST /conversations/{id}/read` | `core_join_requests`、`core_chat_messages`、`core_chat_reads` |
| 拼單動態 | `GET /notifications` | 本人相關的 `core_join_requests` 最新狀態 |
| 公開個人頁 | `GET /users/{id}`、`GET /users/{id}/posts` | 公開欄位與指定 UUID 的拼單 |
| 發布／編輯時間基準 | `GET /development/clock` | 資料庫時鐘 |
| 個人示範資料清理 | `DELETE /development/demo` | 私有批次清單與示範標記 |
| 通知及推薦偏好 | `GET/PUT /account/preferences` | `core_account_preferences` |
| 個人聊天室封存與靜音 | `GET /account/inbox`、`PUT /account/inbox/{id}` | `core_inbox_preferences` |
| 沒興趣及 App 內截止提醒 | `GET /account/saved-posts`、`PUT /account/saved-posts/{id}` | `core_saved_post_preferences` |
| 私人回報 | `GET/POST /reports` | `core_reports` |
| 明確偏好排序 | `GET /recommendations` | 本人偏好與進行中的公開拼單 |
| 公開人物／私人訊息／自己的結束紀錄 | `GET /search?scope=users\|messages\|history&q=...` | 公開 profile 或目前有權查看的資料 |

完整欄位與錯誤格式見 [core.openapi.json](core.openapi.json)。分頁使用伺服器提供的 cursor，不能用目前畫面最後一筆的裝置時間自行組合。發布與聊天送出使用 Idempotency-Key；修改拼單使用版本號避免覆蓋別人的變更。

## 聊天、動態與公開資料

1. 「訊息」顯示自己主揪與已獲接受的拼單聊天室。也可從拼單詳情按「進入拼單聊天室」。待接受者不能讀取聊天，退出後聊天室會從該團員清單移除。
2. 七筆示範中，便當有兩則示範對話，咖啡有一則。示範人物不能登入、不會自動回覆；與真人測試雙向聊天時，請使用另一個允許的 Google 測試帳號。
3. 可傳 1～2,000 字文字，每人每分鐘最多 30 則。網路失敗保留草稿；同一草稿重試沿用相同 key，不重複新增訊息。聊天室會定期同步，右上可手動重新整理。
4. 已取消的拼單聊天室為唯讀；截止後仍可由主揪與已加入者協調取餐。退出者不能再讀取新訊息，也不能使用舊登入憑證繞過資料庫的有效 session 檢查。
5. 訊息徽章是實際未讀數量，拼單徽章是實際待審核數量。已讀標記只移到已取得的訊息，晚到的新訊息仍未讀；延遲送出的舊標記不能把已讀位置往回移。
6. Drawer 的「拼單動態」顯示本人申請與自己主揪之申請的最新狀態，可點進詳情。這是目前狀態清單，沒有宣稱保存完整事件歷程，也不會發送手機推播。
7. 首頁頭像連到按 UUID 取得的公開資料與該使用者的拼單。同名使用者不會混在一起；公開回應不含信箱或登入欄位。等級、完成次數與評價沒有用展示數字充當真實資料。

## 截止時間基準

本輪量測發現裝置與資料庫時鐘有明顯差異；沒有修改作業系統或雲端系統時間。新增／編輯表單會先呼叫 `GET /api/v1/development/clock`，以同一個資料庫時間初始化截止時間與判斷是否過期。收到時間後以 monotonic elapsed time 推進，避免裝置時間突然改變影響判斷。差異大於一分鐘時表單顯示服務時間提示；尚未取得有效時間時不能發布。

預設截止為服務時間至少三小時後，再向上取整至 15 分鐘。伺服器仍會驗證「晚於現在且在 14 天內」，不相信前端自行判斷。既有成功發布但回應遺失的同內容重試仍交由伺服器的 idempotency 判斷，不會因前端截止判斷而重複建立。

## 在 Supabase 畫面查看

進入 `nccu-grouping-dev`：

- **Authentication → Users**：新註冊的雲端帳號與信箱驗證狀態。
- **Table Editor → public**：`core_profiles`（名稱／介紹／圖示）、`core_posts`（拼單）、`core_join_requests`（申請／加入狀態）、`core_chat_messages`（文字訊息）、`core_chat_reads`（個人已讀位置）。
- **Database → Functions**：公開入口 `core_dispatch`。內部函式在 `core_private` schema。
- **SQL Editor** 可只讀查詢：

```sql
select id, author_id, payload->>'content' as content, capacity, cancelled, version, created_at
from public.core_posts order by created_at desc;

select post_id, requester_id, status, created_at, decided_at
from public.core_join_requests order by created_at desc;

select post_id, sender_id, content, created_at
from public.core_chat_messages order by created_at desc;

select id, demo_batch, payload->>'content' as content,
  core_private.post_view(id,author_id)->>'status' as status,
  core_private.post_view(id,author_id)->>'joined' as joined
from public.core_posts where demo_batch='showcase_20261017_v1';
```

資料表的 RLS 已啟用，匿名不能讀取。用戶只能透過受限 RPC 寫入；核准、退出、編輯、取消都在同一資料庫交易鎖定拼單，避免超收或修改未同意的條件。RPC 從 `auth.uid()` 取得身份，並檢查有效 session，不接受客戶端指定操作人的 ID。

## 開發驗證與 migration

- `supabase/core_schema.sql` 是已套用到指定開發專案的新增結構；migration 名稱 `core_grouping_supabase_dev`。不要手動再跑一次，也不要與原先規劃用的 Alembic initial schema 重複套用。
- `supabase/core_validation_hardening.sql` 是後續 `core_rpc_input_validation` migration，補上直接呼叫 RPC 時的格式錯誤處理與發布重試驗證。
- `supabase/core_smoke.sql` 使用單一交易建立虛構 Auth/session fixtures，驗證角色、RLS、發布、編輯、核准、退出與個人資料，最後 `ROLLBACK`，不保留測試帳號或資料。
- `tests/test_core_cloud.py` 的 HTTP 全部使用 mock；pytest 不會連線雲端或寄信。既有 SQLite 與偷揪測試也繼續保留。
- `scripts/smoke_cloud_core.py` 是明確手動執行的線上整合測試，需要另行準備 `.local/cloud-smoke-accounts.json` 中的兩個專用 fixture 帳號。不是日常啟動步驟，也不會由 pytest 自動執行。
- `scripts/smoke_cloud_chat.py` 使用三個隔離 fixture 驗證真實聊天、讀取權限、未讀、並行重試與退出後的撤銷，保留的臨時 thread 僅供後續畫面驗證，測完需定向清理。不要把真實帳號填進 fixture 檔。
- 本輪新增的結構檔依套用順序為 `core_demo_schema.sql`、`core_demo_indexes.sql`、`core_history_readonly.sql`、`core_chat.sql`、`core_chat_variable_scope.sql`、`core_chat_activity.sql`、`core_private_data_policies.sql`、`core_public_users.sql`、`core_order_activity.sql`、`core_server_clock.sql`。皆已記錄 migration；這些不是日常啟動命令。
- 對應 `*_smoke.sql` 以交易回滾驗證角色、資料隔離、分頁、限流、已讀單向推進與不洩漏信箱，不會清理留下給你操作的示範資料。

前一輪三小時核心拼單任務的結果為後端 **197 passed**、前端 **75 個測試案例**通過，TypeScript 與 ESLint 通過；當時產物在 `.expo/three-hour-final/`。後續偷揪整合的最新結果與產物見下方偷揪章節。iOS 尚未執行 Xcode 原生建置或實機驗證，這些結果不代表已完成 TestFlight。

真實 Supabase Auth + 本機 API 已驗證登入、發布、核准、退出、取消與登出；另以三個虛構帳號併發測試最後一個名額，以及修改條件和加入同時發生的情境。瀏覽器雙帳號也走過完整流程。這些已確認的測試帳號只作 fixture，不代表已測過公開註冊的驗證信寄送。測試後已依 UUID 與 fixture 標記清除三個帳號及相關拼單、申請；畫面證據保留於 `.local/core-evidence/`。

| 驗證方式 | 本輪實際確認 | 範圍限制 |
| --- | --- | --- |
| 後端單元／API 回歸 | 197 個案例；驗證身份、錯誤處理、聊天輸入與原偷揪／SQLite 模組 | HTTP 全部隔離，不會由 pytest 呼叫雲端或付費 AI |
| 前端回歸與靜態檢查 | 75 個案例、TypeScript、ESLint；並行續期、延遲 401、登出後舊回應、失敗訊息重試與直接連結返回 | 不等於 iPhone 實機測試 |
| 雲端 SQL 交易測試 | 成員權限、撤銷 session、聊天分頁／限流、已讀單向推進、清理的帳號隔離、公開資料不含信箱 | 隔離 fixtures 最後回滾 |
| 真實 API／瀏覽器 | 主揪核准後名額改變；雙帳號聊天；斷線草稿重試；發布表單使用服務時間且資料確實入庫 | 僅使用本輪專用測試帳號，不操作你的 Google 身份 |
| 最終打包 | Web 與 iOS JavaScript 匯出成功；打包後 Web 登入頁可正常開啟 | 沒有 Xcode 簽署、TestFlight 上傳或原生 Google 回跳驗證 |

`chat-two-account-sync.png` 顯示兩個測試帳號的實際對話，`chat-offline-draft.png` 顯示失敗後保留草稿，`showcase-approval.png` 顯示獨立示範副本的審核結果。這些圖片不是留下給你的七筆資料之即時截圖；相應臨時帳號與副本已清除。最終資料庫確認保留七筆示範拼單、三則示範訊息，臨時 fixture 帳號為零。

本機仍只監聽 loopback，因此 iPhone 尚不能直接連 Windows API；實機連線與 TestFlight 建置需另處理，這次不對外開放連線。

## 下一階段順序與界線

1. 先由團隊用兩個允許登入的 Google 測試帳號走一輪發布、申請、接受、聊天室與退出；示範人物不會自動接受申請或回覆。記錄遇到的操作問題後修正。
2. 接著處理 iOS 原生 Google 登入回跳與 SecureStore 實機驗證。Windows 本機 API 的 loopback 限制仍保留；另行決定明確受控的手機測試連線方式，不能直接把服務改成對外監聽。
3. 在 Mac 執行 Xcode 建置、iPhone 安裝與 TestFlight 內部測試。這次只有 iOS JavaScript 匯出，沒有簽署、上傳或承諾原生建置已完成。
4. 偷揪已支援同一套雲端身份的分身、需求、配對與全員確認費用／分工後的成團交易。後續再評估真正模型的抽取與解釋品質；沒有呼叫 OpenAI，也沒有替示範人物啟用 AI 自動回應。

目前聊天以畫面在前景時約每 10 秒查詢訊息，徽章約每 15 秒查詢；不是 WebSocket／Supabase Realtime，也不保證背景或鎖定畫面即時更新。免費測試階段先維持此方式，後續再依實際使用量調整。

媒體上傳、付款、推播、檢舉／回報後端、帳號刪除與對外公開測試所需的寄信設定仍未完成。頂端「其餘為展示版」表示這些既有展示介面不能當作已串接服務使用。示範資料不是訂購承諾，也沒有付款動作。

最後的 Supabase Security Advisor 沒有發現新增資料表的 RLS／函式設定警示；既有 [Leaked Password Protection Disabled](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection) 警示仍在，這輪沒有更動 Auth 設定或啟用付費項目。Performance Advisor 只有新建私有批次 owner 索引尚未使用的 INFO，保留外鍵索引供後續資料量增加時使用。

## 偷揪雲端版：分身 → 需求 → 配對

入口是 App 選單的「偷揪 · 我的 AI 分身」，或登入後開啟 `http://localhost:8081/stealth`。沿用已登入的 Google／信箱帳號，不建立另一套身份，也不需要新增服務帳號或模型額度。

### 單一帳號快速體驗

1. 「我的分身」設定名稱、外觀與口吻。預設不開啟偷揪、不接受示範配對。
2. 明確勾選「開啟偷揪」與「也接受清楚標示的示範配對」，儲存。測試若碰到安靜時段，可自行取消安靜時段並儲存；不是修改作業系統時鐘。
3. 到「我的需求」，選「我需要」、正餐、政大正門，輸入內容，保留晚餐標籤；預算填 120 元，已知費用留白，勾選一起分食，時間先保留一小時後開始、持續兩小時。
4. 按「確認需求並交給分身」，再到「偷揪結果」。示範資源有每人 90 元的正餐資源、優惠／下單角色、順路取餐角色，能看到兩至三人的互補方案及仍需確認事項。
5. 可以表達意願、拒絕或撤回需求。撤回需求、暫停分身、變更探索範圍／示範授權或超過邀請期限後，舊機會失效。

示範人物沿用先前的三個不可登入人物，沒有新增真人身份、密碼、信件或回覆。只有主動勾選示範的使用者會被納入這些候選；所有包含示範人物的結果都有 `is_demo=true`。示範人物不會接受意願，因此不能用示範結果假裝已成團。過期的示範資源會在使用者明確允許示範並再次尋找時更新有效區間；只更新帶指定標記、無密碼且無 identity 的虛構資源。

### 雙帳號體驗

兩個團隊 Google 測試帳號各自建立並開啟分身，不勾選示範。甲建立正餐需求、正門、相容時間、預算 120、晚餐標籤；乙建立相同分類／地點／時間的資源，已知每人費用 90，明確願意下單與取餐。乙新增時就會嘗試配對，甲不用發文也能在約十秒後看到結果。兩人可以先表達意願，再依下方「最終確認與正式成團」步驟操作。

單純表達意願不會建立核心拼單或聊天室。必須全員另外確認同一版費用、分工與公開內容，才會成團並公開約定的內容；原始私密訊號仍不會複製到公開拼單。這輪沒有搬移舊本機提案／封鎖紀錄；雲端全 App 封鎖管理已整合；舊本機封鎖不會自動上傳。若沒有符合條件的其他需求，沒有配對是正常結果。

### 資料與 API

| 資料表 | 儲存內容 |
| --- | --- |
| `core_stealth_agents` | 帳號 UUID、名稱／外觀／口吻、明確啟用、示範授權、安靜時段、每日上限、設定版本與政策版本 |
| `core_stealth_signals` | 本人需求／資源／行程、分類、本人接受的替代類型、固定集合點、時間、預算、角色、飲食聲明、狀態與重試編號 |
| `core_stealth_proposals` | 兩至三人方案的私有成員／訊號快照、政策版本、條件分數、理由、未知事項、意願、最終條件版本、逐人同意版本與已建立拼單連結 |

三張表的直接存取權限已撤銷，RLS 明確拒絕直接讀寫。App 僅經 `public.core_stealth` → 私有函式執行受限操作；每次驗證 `auth.uid()` 與有效 session，不接受 caller 提供操作人的 UUID。查詢只回本人資料與本人參與的安全配對 DTO。

API 前綴 `/api/v1/stealth-cloud`：`GET/PUT /agent`、`POST /draft`、`GET/POST /signals`、`DELETE /signals/{id}`、`POST /matches/run`、`GET /matches`、`POST /matches/{id}/decision`、`PUT /matches/{id}/terms`、`POST /matches/{id}/consent`。完整契約在 `core.openapi.json`；舊的 `/stealth/` 與 `stealth.openapi.json` 仍是地端原型契約。

需求送出沿用 App 的 12～100 字元 Idempotency-Key，後端確定性轉成 UUID，再與 owner 一起保護重試。前端凍結相同草稿的時間區間及 key，時間推進後的重試仍不重複新增。需求先 commit，再嘗試配對；冷卻、安靜、停用或暫時故障都會回報「需求已保存」及實際配對狀態。

### 配對邊界與驗證

- 沿用原型的分類交集、時間交集、同集合點、至少一個需求、不同使用者、已知費用／預算與必要飲食聲明規則。純資源＋純行程不會被捏造成需求。兩人純需求在熟悉模式還要有共同標籤。
- 每人最多十筆有效訊號，手動配對每三十秒一次，每次最多五組。候選有界，先評分再套用每位真人的每日上限；分數是啟發式，不是成團機率。
- 每個錨點最多二十個候選與每組最多二十個第三方候選，沒有全量最優保證。近期需求／配對各顯示五十筆。
- 生成邀請最多保留三十分鐘；所有接受操作會再次確認有效性。改名字／外觀／口吻不撤銷意願，改啟用／探索／示範授權會更新政策版本並使舊結果失效。
- 免費 provider 做關鍵字分類／集合點提示；另有下方的費用／分工規則規劃器，皆為 `is_llm=false`。沒有 Apple Foundation Models、Ollama 或 OpenAI 呼叫，也沒有下載模型。
- 新增功能的寫入以獨立 advisory transaction lock 序列化，既有拼單／聊天不使用這個鎖。競爭時回可重試錯誤，不改變既有功能的交易。

前一輪本機驗證：後端 **204 個案例通過**、前端 **80 個案例通過**，Ruff、TypeScript、ESLint 通過；Web／iOS JavaScript 匯出到 `.expo/stealth-cloud/`。`core_stealth_cloud_smoke.sql` 的真實 DB 測試涵蓋權限、重試、二人／三人互補、成本／時間／地點／飲食衝突、安靜時段、撤回、示範 opt-in 與 session 撤銷，最後回滾。

瀏覽器已以隔離帳號完成建立分身、儲存需求、三組示範配對、另一測試帳號提供資源後的私人配對、雙方表達意願及撤回。新舊 API 的重試 key 格式相容性也透過真實畫面修正並驗證。`stealth-cloud-matches.png`、`stealth-cloud-reasons.png`、`stealth-cloud-consent.png` 留在 `.local/core-evidence/`；對應兩個臨時帳號、四筆臨時提案、需求、session 與本機測試憑證已清除。最後確認仍保留七筆原拼單、三則原聊天訊息與三筆可 opt-in 的偷揪示範資源，沒有替你的 Google 帳號建立或開啟分身。

套用順序：`core_stealth_cloud.sql`、`core_stealth_cloud_scope.sql`、`core_stealth_cloud_demo_optin.sql`、`core_stealth_cloud_rank.sql`、`core_stealth_test_isolation.sql`、`core_stealth_demo_refresh.sql`，皆為已記錄 migrations，不是啟動指令。`core_stealth_demo_seed.sql` 只供既有虛構人物建立示範訊號，不要重跑以重置使用者的結果。

安全 Advisor 仍只有既有的密碼外洩檢查設定警示；新增表與函式沒有新增安全警示。Performance Advisor 另有新提案索引尚未使用的 INFO，符合目前資料量很小的開發狀態。

## 最終確認與正式成團（前一輪）

工作預算：10 分鐘確認資料／權限邊界、20 分鐘版本化條件與成團交易、20 分鐘 App 確認畫面與入口、10 分鐘回歸／實測／清理。範圍只包含本機程式與指定 Supabase 開發專案，不部署、不使用付費服務、不操作真人 Google 身份。

### 用兩個帳號測試

1. 依上方指令啟動後端及 App。用 Chrome 一般視窗與無痕視窗（或 Chrome／Edge）各登入一個團隊測試帳號。同瀏覽器的兩個一般分頁會共用登入狀態，不能當作兩個獨立帳號。
2. 兩人開啟分身，測試時關閉安靜時段，不勾選示範。依上方雙帳號步驟建立相容需求及資源；兩人都到「偷揪結果」查看同一組機會。
3. 配對卡會標示自己的固定成員代號，並自動帶入分身草案。任一成員修改／補齊「成團公開說明」、每人整數費用、下單／取餐成員及截止時間，按「提出方案，請全員確認」。每人金額相同，0 元可表示免費；若調整配對時的已知價格，需填 5～120 字原因，且不能超過共同預算。
4. 提案者是本版主揪，但**提出方案並不等於同意**。每人都需確認同一版的每人／全團金額、自己的分工、截止與公開內容，勾選後再按「同意第 N 版並等待成團」。分配角色只是提案，本人確認後才代表承諾。
5. 缺任一人時只顯示同意人數，不會建立拼單或聊天權限。最後一人同意後顯示「全員已同意，拼單與聊天室已建立」，可直接開啟正式拼單與聊天室；拼單人數包含主揪，其他成員直接成為已加入者，無須再次申請。
6. 成團前修改方案會增加版本並清除所有舊同意；編輯者成為新版主揪。舊版確認會被拒絕，必須重新整理並確認新版。撤回意願會使這組失效；撤回需求或停用分身也會阻止尚未完成的成團。
7. 成團後沿用原本的截止前退出／釋出名額、主揪取消與聊天室權限。已使用的需求標記 `consumed`，重疊機會失效；退出不會復活舊需求或重複成團。如需再找機會，請另外建立新需求。

示範人物沒有可登入身份，無法提供真人同意；包含示範人物的方案不提供正式成團操作，API 也會拒絕。單一帳號只能體驗配對，不能讓系統替示範人物同意。

### 資料與交易保證

新增 migration：`core_stealth_formation.sql`、`core_stealth_formation_location_note.sql`。在既有提案表新增 `terms`、`terms_revision`、`terms_author_id`、`terms_acceptances`、`formed_post_id` 與 `formed_at`；訊號增加 `consumed` 狀態。沒有另建一套拼單或聊天室。

`public.core_stealth_formation` 是 security-invoker 的入口，私有實作驗證當前非匿名使用者及有效 session。`terms` 只接受公開說明、每人金額、費用調整原因、截止時間、下單／取餐成員代號；不接受客戶端指定 host UUID、owner UUID 或已同意人數。最終同意要求 JSON 布林 `true` 與正確版本。

最後一人同意時，在同一資料庫交易內重新驗證訊號／政策版本、有效期與撤回狀態，建立一筆 `core_posts`、其餘人的 accepted `core_join_requests`，消耗訊號並連結提案。聊天室沿用既有「拼單主揪＋已加入成員」模型，因此不需額外聊天表或偽裝真人發送系統訊息。任一步失敗整筆回滾。

成團與需求撤回、政策更新使用同一功能專用交易鎖；不改原拼單／聊天的交易鎖。重送最終同意會回傳已建立的同一筆拼單，包含成團後已過期或已取消的情況，不能再次建團。每組最多 20 版條件，避免無限改版。

成團前只回本人私密原文、成員代號與共同事實。全員最終同意後，公開拼單才包含大家確認的公開說明、每人金額、主揪與分工者的顯示名稱。私人訊號原文、聯絡方式與飲食聲明全文不會複製到拼單。固定集合點目前使用校園地圖參考位置，地址欄明確提示要在聊天室確認實際集合點。

### 本輪驗證結果

- 後端 **210 個案例通過**、前端 **84 個案例通過**；Ruff、TypeScript、ESLint 通過。核心 OpenAPI 共 31 條 paths，原地端偷揪契約仍保留。
- `core_stealth_formation_smoke.sql` 用四個交易內隔離身份，確認三人全員同意、改版清除、拒絕舊版／陌生人／隱含同意／價格衝突、撤回／停用阻止成團、訊號消耗、重疊失效、原退出釋出名額及聊天室權限；最後完整回滾。舊雲端偷揪 SQL 測試也再次通過。
- 真實本機 API＋Supabase 使用兩個隔離臨時帳號建立方案，一人先同意；另一人在 App 勾選並提交後自動建立 2／2 人、每人 90 元的拼單，開啟聊天室並實際送出測試訊息。四次並行重送仍只對應同一筆拼單。
- Web 與 iOS JavaScript 離線匯出至前端 `.expo/stealth-formation/`，沒有 Xcode 簽署、TestFlight 上傳或 iPhone 實機驗證。
- 畫面證據留在後端 `.local/core-evidence/stealth-final-consent.png`、`stealth-formed.png`、`stealth-formed-post.png`、`stealth-formed-chat.png`。臨時身份、session、提案、需求、拼單、聊天訊息與本機憑證檔都已清除；原七筆示範拼單、三則聊天訊息、三筆偷揪示範需求保留。

仍未代為訂餐、付款、通知真人或呼叫大型模型。原本的登入、一般拼單、聊天、SQLite 與展示模式保留；目前只有 localhost 可用。

## 分身自動提出費用與分工草案（本輪一小時任務）

工作預算：10 分鐘確認既有配對與隱私邊界、20 分鐘免費規劃器、15 分鐘 App 預填／人工修改、15 分鐘回歸／實測／清理。所有程式仍在本機執行，只使用指定 Supabase 開發專案，沒有部署、模型下載或付費呼叫。

### 現在怎麼操作

1. 登入後到「偷揪結果」。有效配對自動顯示「分身自動提案 · 費用與分工草案」，不需額外按生成或扣模型額度。
2. 草案列出已知每人費用、全團金額、共同預算上限、下單／取餐建議與理由。下方編輯區會預填草案，尚未送出，也沒有任何人的同意。
3. 修改公開說明、金額、角色或截止時間後，按「提出方案，請全員確認」。未知費用保持空白；沒有明確志願者的工作保持未指定，補齊才可送出。仍可不採用建議、自行協調後提出方案。
4. 若將配對時的已知價格改成另一個金額，需填至少 5 字、最多 120 字原因，且不能超過共同預算。確認畫面會同時顯示配對時價格、本版金額與原因；每人都要確認新版。原因只對原配對成員可見，不複製到公開拼單。
5. 已有人工方案時，以人工方案為主；只有編輯時才再次顯示原始分身建議。重新整理／輪詢不會自動覆蓋未儲存修改；「還原分身草案」按鈕明確標示會取代未儲存修改，且不會自動送出。
6. 提案者也要另外勾選並同意。全員同意同一版後，沿用既有交易建立正式拼單與聊天室。任何修改會清除舊同意；草案規劃器不會替真人或示範人物同意。

單帳號可沿用上方 opt-in 示範步驟預覽費用／分工草案，包含虛構資源的結果仍明確標示示範，不能正式成團。真人成團需兩個或三個獨立登入環境，不能用同瀏覽器兩個一般分頁模擬不同帳號。

### 規劃器實際會做什麼

| 情況 | 自動草案行為 |
| --- | --- |
| 已知每人 90 元、三人、共同預算 120 | 建議每人 90、全團 270；120 只作上限，不能當售價 |
| 沒有已知價格，即使預算 120 或 0 | 費用仍為未知／空白，不會猜成 120，也不會猜成免費 |
| 明確已知費用為 0 | 保留每人 0、全團 0，與未知價格區分 |
| 甲願意下單、乙願意取餐 | 分別建議給甲、乙的固定成員代號，並解釋取自其明確角色 |
| 多人可下單／取餐 | 優先由不同人分擔；同分以固定代號排序，所有人得到相同建議 |
| 唯一一人同時願意下單與取餐 | 建議同一人兼任，明確提醒本人確認負擔 |
| 沒有志願下單或取餐者 | 保持待協調，不會指派給目前瀏覽者或憑空推定能力 |
| 有優惠券、飲食聲明或未確認事項 | 保留原來的待確認事項；不自行推算折扣或宣稱商家／過敏驗證 |

這是 `offline_rules_v1` 的確定性規則規劃器，`is_llm=false`，不是大型語言模型推論。新模組 `app/core_cloud/planner.py` 不連網、不讀私人原文、不建立方案版本、不改同意紀錄。相同配對快照會產生相同草案；後續接 Apple／本機模型時仍需保留相同授權與成團檢查。

公開說明草案只帶餐點分類、集合點與確認流程，金額放在結構欄位，不嵌入可變的價格文字，避免人工改價後說明仍保留舊金額。全團金額目前是相同每人金額乘人數；不同人份額、退款及代付結算仍需自行協調。

### 資料與 API

新增 migrations：`core_stealth_planning_context.sql`、`core_stealth_reviewed_cost.sql`，皆已套用到開發專案。沒有新增服務或草案資料表，也沒有新增背景 worker。

原本的私有配對 view 只在有效機會中取出固定成員代號與已宣告角色，提供後端規劃器；不取其他人的 UUID、名字、原始文字或聯絡方式。`planning_members` 不會出現在 FastAPI 的公開回應；受限 Data API 的本人配對 RPC 也只會提供這些去識別角色事實。原本 session／成員檢查及表格 RLS 維持。

`GET /api/v1/stealth-cloud/matches` 與既有配對寫入回應增加 `agent_draft`：provider、`is_llm`、每人／全團金額、費用依據、預算上限、截止、下單／取餐代號、候選代號與理由、缺少欄位及待確認事項。已成團或失效時為 `null`。這是讀取時從已驗證快照計算的建議，不是自動保存的最終條件，因此瀏覽結果不會清除同意或產生公開拼單。

`PUT /matches/{id}/terms` 的 `value` 另接受 `cost_change_note`；舊客戶端不傳此欄位仍可使用相同價格。調整已知價格時，資料庫也強制檢查原因及預算，不能繞過 App 驗證。手動價格變更不會修改原配對時的價格，確認頁才能保留兩者比較。改版、最終同意與交易內成團規則保持。

### 本輪實際驗證

- 後端 **222 個案例**、前端 **88 個案例**通過，Ruff、TypeScript、ESLint 通過；核心契約仍是 31 條 paths。
- 規劃器測試涵蓋未知費用／零元、預算不可當售價、不得臆測優惠、缺少角色、多人分工與固定排序、隱私原文排除、無自動同意，以及失效／成團後不再出新草案。
- 真實 DB 的原偷揪／正式成團交易測試再次通過；另驗證去識別角色 context、改價無原因被拒絕、原因／預算檢查及改價清除舊同意，所有交易 fixtures 回滾。
- 三個隔離臨時帳號收到完全相同的三人草案：每人 90、全團 270，下單與取餐各由明確志願者負責。App 修改公開說明後，全員確認才建立 3／3 拼單與所有成員的聊天權限。
- 另一組隔離配對有預算 120，但價格、下單者、取餐者皆未知；App 仍保持空白／未指定，送出按鈕不可用。人工方案及未儲存修改也不會被新草案覆蓋。
- 再以已知價格 90、預算 120 的草案，在 App 改成 100：缺少原因不能送出，補上原因並由雙方確認後才建立每人 100 的 2／2 拼單；公開拼單沒有費用調整原因或私人原文，也沒有殘留 90 元的草案文字。
- 最終 Web／iOS JavaScript 離線匯出至前端 `.expo/stealth-agent-draft-final/`，沒有簽署、部署、上傳或 iPhone 實機驗證。
- 截圖在後端 `.local/core-evidence/stealth-agent-draft.png`、`stealth-agent-draft-unknown.png`、`stealth-agent-cost-review.png`、`stealth-agent-reviewed-post.png`。三個臨時身份、session、需求、配對、兩筆成團拼單及本機測試憑證檔已清除；原七筆示範拼單、三則訊息與三筆偷揪示範資源保留。

Security Advisor 沒有新增警示；既有的密碼外洩檢查設定警示仍未改動。上一輪沒有消耗 OpenAI API 額度，也沒有下載或啟用其他模型。

## 免費本機模型理解與補問（2026-10-17，一小時工作預算）

順序安排：前 10 分鐘確認環境與官方模型來源、15 分鐘結構化理解及補問、15 分鐘 App 待確認表單、20 分鐘真實模型／Supabase／回歸測試與清理。不部署、不使用付費模型。此輪沿用既有資料表與成團 RPC，沒有資料庫 migration。

### 現在的完整流程

1. 開啟分身並儲存，到「我的需求」的「先跟分身說說看」，輸入「我想找人一起吃披薩，但我不方便取餐」。
2. 本機模型整理語意，後端根據缺少欄位補問地點、相對開始時間、持續多久、預算與自願角色。可一次用完整句子回答多個問題，例如「政大正門，一小時後開始，可以配合兩小時。每人預算120元，願意分食，不下單也不取餐」。
3. 資訊足夠時按「套用到下方表單，逐項檢查」。這一步尚未寫入資料庫。檢查費用、時間、角色與飲食條件，再按「確認需求並交給分身」才寫入既有 Supabase 需求流程；成功後清除暫存對話。
4. 配對後沿用已驗證事實產生費用與分工草案。模型不能把預算當報價、宣稱已驗證食物、替人同意或直接成團。每人仍需確認同一版方案，最後一人才觸發既有交易建立拼單與聊天室。

自然語言目前沿用五種餐點分類、三個政大集合範圍；尚未支援任意商家地點。請使用「一小時後」等相對時間；「今晚六點」等絕對或模糊時間會補問，不依 Windows 時鐘猜測。最後儲存仍用資料庫時間。小模型可能漏讀或誤解，套用後的逐項人工確認不可省略；共同偏好標籤也可手動補充。

### 啟動方式（Windows CMD）

本機已備妥官方 Ollama v0.40.5 可攜工具與 `qwen3.5:4b`，不需要註冊帳號或 API key。下次重開時，在後端目錄分別開兩個終端：

```bat
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\run_local_model.py
```

```bat
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\run_supabase_core.py
```

前端目錄執行 `npm run dev:supabase`，瀏覽 `http://localhost:8081`。不要重複啟動已在執行的服務；各終端的 Ctrl+C 可停止它。模型執行於 Windows，沒有包進 iPhone，也沒有讓手機直接連模型服務。

新的電腦若未準備模型，先在後端執行 `scripts/setup_local_model.py`，下載官方可攜工具並驗證固定 SHA256；再啟動 `run_local_model.py`，另外一個終端執行 `run_local_model.py pull` 下載官方免費 4B 模型。下載需要網路、數 GB 空間；模型推論不需要網路。套件、模型、狀態及設定皆在 Git 忽略的 `.local/ollama`、`.local/local-ai.env`，不改系統 PATH、執行政策、驅動、開機啟動或防火牆。可用 `run_local_model.py status` 看 CPU／GPU 使用狀況。本輪選用 CPU，不宣稱使用 AMD NPU。

`.local/local-ai.env` 僅包含 `STEALTH_AI_PROVIDER=ollama_local`、`STEALTH_OLLAMA_MODEL=qwen3.5:4b`、`STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED=true`，沒有金鑰。若停止模型，App 顯示可重試訊息，仍可使用原手動表單與關鍵字提示；不會改用付費雲端推論。

### API 與邊界

新增 `POST /api/v1/stealth-cloud/intake`，需原 Supabase 登入 session。輸入 `{"turns":["第一輪需求","之後的補充"]}`，最多八輪、每輪 1,000 字、總共 4,000 字。回傳 `facts`、`questions`、`ready_for_review`、`provider=ollama_local`、`is_llm=true`、模型名稱與待確認提醒；`ready_for_review` 只代表可進入表單檢查，不是保存／授權／成團。

訪談為無狀態，原始對話不寫入 Supabase 或後端檔案，也不送至其他配對者。App 只在記憶體暫存，成功保存、清除或離開需求畫面後清除。Ollama 只監聽 `127.0.0.1:11434`，停用雲端功能與對話內容日誌；後端只連固定 loopback、拒絕遠端模型別名、HTTP 轉址、工具呼叫與不符結構的輸出，且不攜帶 Supabase／OpenAI／MCP 憑證。每次只允許一個推論，忙碌立即回覆可重試；最多等候 90 秒，沒有自動下載模型的 API。

自然語言理解使用 LLM；費用計算、分工候選、配對必要條件、版本與全員確認仍是可驗證的程式規則。既有 `GET /matches` 的 `agent_draft.is_llm=false` 保持正確，不把規則草案冒稱模型推論。沒有更改任何人的 Google 帳號、付款或訂餐操作。

官方來源：[Windows 可攜執行方式](https://docs.ollama.com/windows)、[停用雲端與本機設定](https://docs.ollama.com/faq)、[結構化輸出](https://docs.ollama.com/capabilities/structured-outputs)、[Qwen3.5 模型庫](https://ollama.com/library/qwen3.5)。

### 此輪實測與清理

- 後端 236 個、前端 90 個測試通過，Ruff、TypeScript、ESLint 通過。核心 OpenAPI 增為 32 條 paths，原偷揪原型契約保留。
- 官方可攜執行工具已驗證固定 SHA256。實測比較 2B／4B 後選用 4B：七組合成中文情境涵蓋缺漏補問、後續補充、已知價格、未知預算、後續更正需求、絕對時間及單純路過。4B 在主要四組的 CPU 推論約 10～16 秒；這是目前合成案例結果，不是普遍正確率保證。未採用的 2B 模型已清除。
- 真實 App 完成第一次補問、補充、套用、手動確認儲存。儲存前實際查 DB，隔離身份的需求筆數為 0。另一隔離帳號也經真正本機模型整理並明確儲存，形成每人 90 元／全團 180 元、由自願者下單取餐的配對。
- App 提出方案及第一人明確同意後仍未成團；第二個隔離身份透過 API 明確同意同一版後才建立一筆正式拼單，雙方聊天權限及 App 聊天畫面都正常。重送最終同意仍是同一筆拼單。
- Web 與 iOS JavaScript 以 Supabase 開發模式離線匯出至前端 `.expo/stealth-local-model-supabase/`，未進行 Xcode 簽署、iPhone 實機或 TestFlight 上傳。
- 畫面證據在後端 `.local/core-evidence/stealth-local-model-questions.png`、`stealth-local-model-plan.png`、`stealth-local-model-formed.png`。兩個隔離帳號、sessions、需求、配對、成團拼單與臨時憑證檔已清除；DB 恢復七筆示範拼單、三則訊息、三筆示範偷揪需求，沒有本輪臨時帳號。


## 雲端封鎖與本人刪除帳號（本機程式／開發專案）

個人公開頁提供「封鎖」二次確認；「帳號與隱私」列出本人封鎖名單及解除入口。封鎖會立即清除當前帳號查詢快取，雙向隱藏一般貼文、公開個人資料、使用者搜尋、相關訊息搜尋與新偷揪配對，禁止新申請、接受申請及與封鎖對象共群的新訊息。未接受申請取消，未成團方案失效並清掉舊同意；解除封鎖不恢復它們。封鎖名單不公開。

已接受的團員仍保留必要拼單紀錄與退出權，不自動踢人或取消別人的訂單。群組中被封鎖作者的訊息、預覽與未讀數會過濾；其他未涉及封鎖的團員仍可發訊息。已發出的圖片簽署網址可能在剩餘有效期（最多60秒）內可用。這不是完整內容治理或管理者停權。

「帳號與隱私 → 刪除我的帳號」先顯示影響數量與警告，再取得10分鐘確認碼，手動輸入大寫 DELETE 才可執行。登入需在15分鐘內，refresh 不算重新登入。確認碼綁定本人及該次登入，資料庫只存雜湊；預覽／取消不會開始刪除。開始後凍結一般操作，先以本人 JWT 經 Storage API 移除真實圖片，再在交易中刪除 Auth、sessions、refresh tokens、profile、偏好、本人訊息、分身／需求及相關配對。其他人的拼單與訊息保留；本人主揪及本人示範批次的拼單與相關聊天室移除，其他人的名額會釋出。全員確認規則維持原樣。

清理中斷不顯示成功。重新登入同一帳號會進入專用復原畫面，重新輸入 DELETE 可繼續；已清掉的圖片可以安全重試。其他 bucket 殘留檔案會阻止完成，不能把 storage.objects 資料列刪掉冒充實體清理。刪除不會刪 Google 帳號、手機照片、他人保存副本或引用；正式備份／日誌保留期限仍需另訂。再次 Google 登入會建立新的 App 帳號。

新增 API（皆使用本人登入，不接受 owner_id）：GET /account/blocks、PUT／DELETE /account/blocks/{user_id}；GET /account/deletion、POST /account/deletion-preview、DELETE /account（JSON confirmation=DELETE、首次帶 challenge）。API 合約見 core.openapi.json。沒有使用 service_role、付費服務或部署。

依序已套用 core_user_safety.sql、core_account_deletion.sql、core_account_deletion_storage_access.sql、core_account_deletion_storage_serialization.sql；已套用檔案不可重跑或覆寫，修正要另增 migration。所有 app RPC 驗證目前 auth.sessions，舊 private RPC 不能直接繞過。Storage 與刪除使用同一交易鎖序，避免清理時新增圖片；目前採全域序列化以保護小型開發專案，正式大量流量需評估吞吐。

驗證：334個後端、118個前端測試；三個隔離 Auth 身份實測封鎖、共享群組、第三人可用、公開／草稿實體圖片清理、登出重新登入續作、舊憑證拒絕。另以 core_user_safety_smoke.sql、core_account_deletion_smoke.sql、core_account_deletion_edges_smoke.sql 交易回滾測試逾期／跨登入確認碼、舊登入、偷揪及示範批次 FK、成團分工去識別。全部隔離身份、資料及本機測試憑證檔已清除；原5個 Auth／profile、7筆示範拼單、3則訊息、3筆示範需求保留。這不代表已完成 iPhone 原生或 TestFlight 驗收。


## 雲端部署準備（尚未部署）

後端新增獨立 `scripts/run_cloud_core.py`，不再以放寬本機 runner 的方式部署。ENVIRONMENT=cloud 與 CORE_CLOUD_ENABLED=true 必須同時明確設定；本機／SQLite／iPhone內網 runner 保持原功能，且會關閉雲端模式。此次僅程式、環境與合成請求測試，沒有修改 Supabase、執行 migration、開放網路、部署或消耗付費服務。

### 最短設定與檢查

1. 將 `config/cloud.env.example` 私下複製成 `.local/cloud.env`。
2. 填入真實 CLOUD_PUBLIC_API_URL（精確 HTTPS DNS 網址，以 /api/v1 結尾）及 SUPABASE_PUBLISHABLE_KEY。仍只允許指定開發專案 axdwbfhrkbovgrbngrrh，不會自動轉到其他專案。
3. CLOUD_PROXY_NETWORKS 填入直接連到 API 的 TLS gateway IP／窄網段，不是 iPhone IP；不得使用 *、0.0.0.0/0 或寬泛全內網。預設127.0.0.1/32只適用同機gateway。跨容器／平台需填真正的立即上游範圍並隔離API埠。
4. 原生 Google allowlist 完成後才將 CORE_GOOGLE_IOS_ENABLED 設 true；保持 CLOUD_GOOGLE_WEB_CALLBACK 空白及 BACKEND_CORS_ORIGINS=[] 即為原生 App 模式。後端只預留HTTPS Web callback；現有Web前端仍限制localhost，本輪沒有把Web登入改成雲端網站。
5. 使用圖片時另安全移交目前資料庫對應的 CORE_MEDIA_SIGNING_KEY（64字元小寫hex），再設定 CORE_MEDIA_ENABLED=true。不能重新產生取代舊驗證金鑰。沒有此金鑰時純文字流程與本人刪除清理仍可使用。

```cmd
.venv\Scripts\python.exe scripts\run_cloud_core.py --env-file .local\cloud.env
```

Mac／Linux 使用 `.venv/bin/python`。預設只有檢查，不建立 listener、不連 Supabase、不執行 Docker、不產生雲端資源。模板中的佔位網域／key會被拒絕，這是預期保護。錯誤只列設定欄位或固定說明，不列印密鑰值。雲端讀取程序環境及指定檔案，不自動讀工作目錄 .env 或 Windows .local 設定。

可加 `--write-client-profile` 匯出 `.local/apple-release.json`；只有 profile=release 與公開 API 網址，沒有任何key。後續帶到Mac，複製為前端 `.local/apple-dev.json` 後執行 `npm run apple:check`、`npm run apple:device`，或直接用既有 apple:configure 指令填相同網址。App重新編譯才會套用網址；設定檢查成功不代表伺服器已可達。

### 未來真正啟動的唯一入口

```bash
python scripts/run_cloud_core.py --serve
```

此指令留給後續已授權的部署，本輪沒有執行。主機需提供所有環境變數；CLOUD_BIND_HOST預設127.0.0.1，只有容器／私有網路需要時明確設0.0.0.0，PORT由平台填入。API不能直接公開在HTTP埠；必須由受信任TLS gateway連入，對外只提供HTTPS。代理必須保留 /api/v1 路徑，不使用root-path重寫。

`config/nginx-cloud.conf.example` 提供單一TLS ingress合約及邊緣限流範例，放在Nginx http context；沒有安裝或啟動。代理必須覆寫 Host、X-Forwarded-Proto=https、X-Forwarded-For=實際使用者單一IP；X-Forwarded-Host若提供必須一致，移除 Forwarded／X-Real-IP。不能用 proxy_add_x_forwarded_for 拼接不可信標頭。程序關閉 Uvicorn 自動 proxy headers；由自己的middleware核對真實socket peer、Host與轉送資料。直接HTTP、未知代理、錯Host、轉送鏈／重複關鍵標頭與未允許Origin均拒絕。

如果代管平台使用多層代理或無法覆寫成此合約，需先按該平台官方網路資料調整與重測；不能把信任範圍改成萬用字元。平台／gateway與API的網路隔離是部署必須驗收的部分，單靠env檢查無法證明。

### 執行與成本限制

- 第一版只支援一個程序、一個實例；CLOUD_WORKERS／CLOUD_INSTANCES必須是1，WEB_CONCURRENCY／UVICORN_WORKERS若提供也只能1，禁止reload。Google PKCE單次proof及限流保存在記憶體，不需要Redis或其他付費服務。平台必須關閉autoscaling、多副本及滾動更新期間的新舊副本重疊；重新啟動或主機休眠會清空proof與程序限流，登入需重新開始。
- 每個已驗證來源IP預設20次登入相關請求／分鐘、240次總請求／分鐘；最多4096個活躍quota key，容量滿時拒絕新增key。計數有鎖與期限，不因任意新IP無限成長。Nginx範例另有邊緣限流，正式平台也需同等保護。
- Supabase Auth另有自己的配額。後端使用Publishable key，因此Supabase可能將所有Auth請求按伺服器出口IP計算；不能假裝傳X-Forwarded-For就改變上游限流。本版本沒有加入secret API key或更動Supabase設定。需在實際測試流量下驗證429與重試。[官方配額說明](https://supabase.com/docs/guides/auth/rate-limits)
- 雲端明確使用免費規則草案／手動需求，拒絕OpenAI key、service_role與ollama_local。分身、配對、費用／分工、改版清空同意及同版全員確認成團保留；自然語言本機Qwen仍只在原本localhost模式使用，不會隨雲端部署自動公開或搬進iPhone。
- 目前不支援雲端WebSocket。核心聊天保留既有前景輪詢；APNs、語音與Apple裝置模型沒有在本輪實作。

### 安全與可觀測性

JSON body上限6MiB（包含base64圖片），即使chunked或偽造Content-Length也限制實際讀取；讀取逾時30秒。雲端限制同時64個連線、短keep-alive，關閉公開docs／schema、本機偷揪及示範清理入口。本人資料仍經既有Supabase JWT／live session及RPC權限，不使用管理key；封鎖、刪除與全員確認規則沒有移除。

回應有no-store、nosniff、HSTS、no-referrer與隨機request ID。雲端HTTP client INFO URL日誌及Uvicorn access log關閉，避免 signed URL／code／token進入常規日誌；Nginx範例只記method、status及request ID。實際平台的代理／錯誤日誌、保留期限與備份仍需另訂，不保證第三方日誌自動去識別。

`/health`是無外部依賴的liveness，只回ok；可供內部HTTP健康檢查。`/ready`需通過TLS代理驗證，回configured／database=not_probed；App既有 /api/v1/development/readiness 也可使用。這些都不代表Auth／資料庫／Storage已通過實際驗收。

部署完成並取得另外授權後，可執行：

```bash
python scripts/check_cloud_api.py --url https://你的API網域/api/v1
# 上面只驗證格式；以下才會真的做兩次無憑證HTTPS GET
python scripts/check_cloud_api.py --url https://你的API網域/api/v1 --probe
```

probe只讀 /health 及 /ready，不寄登入信、不讀帳號資料、不跟隨redirect、不關閉TLS驗證；仍需另外用隔離帳號跑登入、圖片、聊天、封鎖、刪除與偷揪驗收。

### 容器與回復

Dockerfile改為固定uv版本及uv.lock安裝runtime依賴、多階段、非root使用者；只複製app與雲端runner，不攜帶Git、.env、.local、測試、私鑰或migrations。容器啟動不下載模型、不更新schema。密鑰由平台的執行期環境／secret store提供，不能用Docker build ARG或COPY放進image。

此執行環境找不到Docker CLI，因此此次沒有實際build／啟動image，也沒有執行Nginx。未來先做Linuximage build、鎖檔／base image掃描與gateway整合驗證，再安排部署；不能把Python合成測試寫成容器已驗收。正式發行前可進一步固定base image digest。

部署時保留前一個已驗證image與env版本；回復時換回前一版，不自動回退資料表或重建HMAC金鑰。migration維持獨立、逐份審查；不要執行早期Alembic initial schema到既有Supabase專案。資料與備份、監控及恢復演練仍是營運項目。

### 本輪驗證與尚待外部驗收

新增合成測試涵蓋無效env、秘密值遮蔽、單實例限制、代理偽造、Origin/CORS、串流大小限制、限流併發／容量／過期、固定iOS Google回跳、既有使用者授權與HTTPSprobe。沒有對真人或雲端帳號做測試操作。主機、網域、TLS憑證、實際代理IP與防直連、Supabase配額、Linux容器build、iPhone／TestFlight仍待實際環境驗收。Apple登入與完整政策／治理缺口見RELEASE_READINESS.md。

官方實作依據：[FastAPI proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)、[Uvicorn設定](https://uvicorn.dev/settings/)、[uv容器整合](https://docs.astral.sh/uv/guides/integration/docker/)、[Nginx限流](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)。

前一輪雲端準備驗證：401項後端測試通過、Ruff及格式檢查通過、48條API合約離線匯出。兩項既有依賴deprecation warnings保留。Docker／Nginx未執行、沒有對真實雲端endpoint做probe；前端未變更。

## 本機語義配對與可替換 AI 接口

本輪僅改本機程式，沒有新增 Supabase schema、操作真人帳號、部署或呼叫付費模型。三個 Protocol 定義在 `app/stealth/provider_contracts.py`，目前只有免費 loopback Ollama 實作：

| 接口 | 輸入與輸出 | 邊界 |
| --- | --- | --- |
| `IntentInterpreter` | 訪談文字 → `IntakeResult` 事實草稿及缺漏問題 | 沿用抽取證據檢查與本人確認，不自動儲存需求 |
| `EmbeddingProvider` | query／document → 正規化 `VectorBatch` | 模型名稱、digest、512 維及 retrieval-v1 一起識別向量空間；版本不一致拒絕混用 |
| `CandidateReviewer` | 本人需求、合法候選摘要、允許的事實代碼 → `CandidateJudgment` | 只能選受支持理由；缺費用、志願或優惠條件強制待確認；不能授權或執行動作 |

未來 Apple／OpenAI 實作可共用介面，但目前沒有這兩者的可執行 adapter。更換向量模型需重新產生向量；目前逐次計算、沒有持久化向量索引或 pgvector 搜尋。費用、分工草案仍由既有規劃器產生；模型不捏造報價。模型端拒絕、逾時、忙碌或格式錯誤以 `LocalModelError` 安全代碼回報；未來 adapter 應遵守相同錯誤合約。

### 啟用與測試

本 Windows 工作區已安裝 `qwen3.5:4b` 及 `qwen3-embedding:0.6b`。新環境需先安裝 Ollama，再明確下載模型；Windows portable runner 固定工作區內模型目錄、loopback、停用雲端及單筆推論。API 不會自動下載。Embedding 約 639 MB，文字模型約 3.3 GB；不需 API key。官方模型資料：[Qwen3 Embedding](https://ollama.com/library/qwen3-embedding)、[0.6B Apache-2.0 授權](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[Ollama Embed API](https://docs.ollama.com/api/embed)。

在後端專案開終端機啟動模型；已在執行時不要再啟動第二份：

```powershell
.venv/Scripts/python.exe scripts/run_local_model.py
# 新環境且明確需要下載時，另一個終端機才執行：
.venv/Scripts/python.exe scripts/run_local_model.py pull
.venv/Scripts/python.exe scripts/run_local_model.py pull-embedding
```

專案忽略的 `.local/local-ai.env` 設定如下，再重啟本機 API：

```dotenv
STEALTH_AI_PROVIDER=ollama_local
STEALTH_OLLAMA_MODEL=qwen3.5:4b
STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED=true
STEALTH_SEMANTIC_ENABLED=true
STEALTH_EMBEDDING_MODEL=qwen3-embedding:0.6b
```

```powershell
.venv/Scripts/python.exe scripts/run_supabase_core.py
.venv/Scripts/python.exe scripts/evaluate_semantic.py
# 以下兩個指令才會呼叫本機模型；只用固定合成資料，不連 Supabase：
.venv/Scripts/python.exe scripts/evaluate_semantic.py --run-local
.venv/Scripts/python.exe scripts/smoke_local_semantic.py --run-local
```

真實模型評估：固定 12 個檢索案例 top-1 12/12、top-2 12/12；6 個候選判讀案例通過，包括 2 個互補情境、3 個缺資料及 1 個指令干擾。這是初步合成回歸案例，不是正式準確率或過敏安全驗證；其中未知事項由程式強制待確認。可重跑評估，失敗會傳回非零 exit code；時間、模型 digest、逐題排序及判讀存於忽略的 `.local/evals/semantic-latest.json`。案例固定於 `tests/fixtures/semantic_cases.json`，不使用真人資料。隔離 SQLite smoke 已驗證真實模型建立待確認配對，且沒有同意時成團被擋下，測後自動刪除自己的暫存資料庫。

本機本次量測：每題檢索平均約 0.30 秒（0.19–1.16 秒），每筆判讀平均約 3.82 秒（3.09–6.26 秒），僅代表此筆電與已啟動模型的本輪測試，不能當成併發或冷啟動保證。模型共用單筆執行鎖；忙碌時回報，不累積無界排隊。最終後端回歸 430 項通過、Ruff／格式檢查通過；49 條核心 API 及 12 條 SQLite 偷揪 API 合約離線匯出。保留兩項既有測試依賴 deprecation warnings。

### 兩種 API 的差異

| 模式／API | 實際行為 |
| --- | --- |
| 本機 SQLite：`POST /api/v1/stealth/matches/semantic-run`，body `{"max_proposals": 3}` | 硬條件篩選 → 最多 12 個候選算向量 → 判讀前三個 → 依語義順序交回既有交易配對，重新檢查版本、封鎖、期限、限額及冷卻後建立提案 |
| 本機 API＋Supabase 開發資料：`POST /api/v1/stealth-cloud/matches/semantic`，body `{"review_limit": 3}` | 僅重新排序帳號原有權看到的 pending／ready 配對摘要，運算後再讀一次並移除變更／失效項目；不建立配對、不改同意或方案 |

兩者都必須通過既有登入與模式限制。先用既有 `/matches/run` 或需求建立流程產生 Supabase 配對，再呼叫摘要排序。App 現有按鈕與輪詢仍走原配對 API；本輪新增後端明確操作入口，可於本機 Swagger 測試，沒有把昂貴推論放入每十秒輪詢。

Supabase 摘要包含餐點分類、成員數與已授權的志願，不含他人的私人需求原文。因此雲端開發模式目前只能對合法摘要排序，語義辨識細節比 SQLite 原型少，尚未實作跨使用者私人文字的語義候選發現。要改善需另設權限受限的結構化候選摘要與索引，不能為效果直接公開原文。

回應 `ranking.status` 為 completed／empty／unavailable，`similarity` 是餘弦相似度、不是成功率；`review_status` 與 `review_model` 分別記錄判讀結果和模型。模型不可用會保留規則順序、相似度為 null 並附警告，不假裝已做語義理解。排序及判讀是建議，不代替原有全員同意同一版費用與分工的雲端成團交易。本機推論期間不持有資料庫交易。

local 模式才可啟用；device runner 明確關閉此功能，cloud runner 也拒絕啟用。Apple 留到 Mac 實作及實機驗證。OpenAI 未呼叫，也沒有自動切換遠端或付費模型。Mac 可安裝自己的 Ollama 並使用相同模型／評估；Windows 的 `.exe`、模型快取與 `.venv` 不會放入交接 Git bundle。
