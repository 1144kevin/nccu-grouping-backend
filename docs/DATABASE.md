# Database 規劃

使用 Supabase PostgreSQL，並啟用 `vector` extension。所有 schema 變更由 Alembic migration 管理。

## 資料表

| Table | 責任 |
| --- | --- |
| `profiles` | App 使用者公開資料，ID 對應 Supabase Auth user |
| `posts` | 拼單內容、地點、截止時間、人數與狀態 |
| `post_media` | 圖片與語音的 Storage path |
| `join_requests` | 加入申請與審核狀態 |
| `conversations` | 成團後的聊天室 |
| `conversation_members` | 聊天室成員、已讀與靜音狀態 |
| `messages` | 聊天訊息 |
| `notifications` | App 內通知 |
| `device_tokens` | Expo Push Token |
| `user_preferences` | 推薦標籤與通知偏好 |
| `recommendation_events` | 推薦曝光與使用者操作 |
| `post_embeddings` | 貼文 embedding、模型和內容 hash |
| `blocks` | 使用者封鎖關係 |
| `reports` | 使用者產生內容的檢舉紀錄 |

## 資料規則

- Primary key 使用 UUID。
- 時間使用 PostgreSQL `timestamptz` 並儲存 UTC。
- `capacity` 包含主揪本人，範圍為 2 到 20。
- 同一位使用者對同一貼文只能有一筆加入申請。
- 接受申請時使用 database transaction，不能只相信前端顯示的人數。
- 媒體檔案存在 Supabase Storage，DB 只保存 path 與 metadata。
- 第一版標籤使用 `text[]`，不急著拆成額外關聯表。
- embedding 與一般貼文資料分表，方便重新產生或替換模型。

`profiles.id` 會對應 Supabase `auth.users.id`。因為 `auth` schema 由 Supabase 管理，實際 foreign key 會在連上 Supabase 後由 initial migration 確認。
