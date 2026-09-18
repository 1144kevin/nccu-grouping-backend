# API 規劃

Base path：`/api/v1`

Supabase Auth 負責註冊、登入、Email 驗證和 refresh token；FastAPI 驗證 Supabase JWT，並處理所有核心業務寫入。

## 第一階段：核心拼單

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 服務狀態 |
| `GET` | `/me` | 目前使用者資料 |
| `PATCH` | `/me` | 修改個人資料 |
| `DELETE` | `/me` | 刪除帳號 |
| `GET` | `/posts` | 首頁資訊流與 cursor 分頁 |
| `POST` | `/posts` | 建立拼單 |
| `GET` | `/posts/{post_id}` | 貼文詳情 |
| `PATCH` | `/posts/{post_id}` | 修改自己的貼文 |
| `POST` | `/posts/{post_id}/cancel` | 取消自己的拼單 |
| `GET` | `/posts/search` | 搜尋貼文 |
| `POST` | `/uploads/presign` | 取得媒體上傳資訊 |
| `POST` | `/posts/{post_id}/join-requests` | 申請加入 |
| `DELETE` | `/join-requests/{request_id}` | 取消申請 |
| `GET` | `/me/incoming-requests` | 主揪查看申請 |
| `PATCH` | `/join-requests/{request_id}` | 接受或拒絕 |
| `GET` | `/me/orders` | 主揪與參加紀錄 |

## 第二階段：聊天室與通知

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/conversations` | 聊天室列表 |
| `GET` | `/conversations/{id}/messages` | 訊息分頁 |
| `POST` | `/conversations/{id}/messages` | 傳送訊息 |
| `POST` | `/conversations/{id}/read` | 更新已讀時間 |
| `GET` | `/notifications` | 通知列表 |
| `POST` | `/notifications/read-all` | 全部已讀 |
| `POST` | `/device-tokens` | 儲存 Expo Push Token |
| `DELETE` | `/device-tokens/{token}` | 停用 Push Token |

## 第三階段：AI、推薦與安全

| Method | Path | 用途 |
| --- | --- | --- |
| `POST` | `/ai/post-assist` | 生成、潤稿與圖片理解 |
| `GET` | `/recommendations` | 個人化推薦 |
| `POST` | `/recommendations/feedback` | 記錄曝光、點擊、加入與略過 |
| `GET` | `/me/preferences` | 取得偏好 |
| `PATCH` | `/me/preferences` | 修改偏好與通知設定 |
| `POST` | `/users/{id}/block` | 封鎖使用者 |
| `DELETE` | `/users/{id}/block` | 解除封鎖 |
| `POST` | `/reports` | 檢舉貼文、訊息或使用者 |

## API 原則

- 時間一律使用 ISO 8601 UTC，前端負責顯示時區。
- 列表一律使用 cursor pagination，不使用無上限陣列。
- 建立貼文與加入申請支援 idempotency，避免重複送出。
- 接受加入申請必須在 transaction 內鎖定貼文並檢查剩餘名額。
- App 不得直接使用 Supabase service role key。

