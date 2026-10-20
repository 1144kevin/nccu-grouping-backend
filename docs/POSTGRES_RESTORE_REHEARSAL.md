# 本機 PostgreSQL 備份還原演練

2026-10-20 已實際執行，結果見 [驗收證據](evidence/postgres-restore-20261020.json)。全程沒有連線、匯出或覆蓋 Supabase 現有資料，也沒有部署、寄信或呼叫模型。

## 如何重跑

先啟動 Docker Desktop，於後端目錄執行：

```powershell
.venv\Scripts\python.exe -m scripts.rehearse_postgres_restore
.venv\Scripts\python.exe -m scripts.rehearse_postgres_restore --run
```

第一行只說明、不建立資料庫。第二行才下載官方 `postgres:17-bookworm` 並建立兩個隨機名稱容器；只允許 default context 的本機 Linux socket，不接受資料庫 URL、外部 dump 或還原目的地參數。若找不到 docker，依 [Windows 設定](WINDOWS_CONTAINER_SETUP.md) 重開終端機或補本次視窗 PATH。Mac 在建立自己的虛擬環境後改用 `.venv/bin/python`。

兩個容器均無網路、無發布埠、無 host mount、無持久 volume；以 UID999、唯讀根目錄、移除 capabilities、no-new-privileges 與資源上限啟動，資料庫只存在記憶體 tmpfs。PostgreSQL 的 TCP 監聽也停用。測試使用僅容器內 Unix socket 可用的 trust，不可把這項設定搬到公開主機。

## 實際涵蓋範圍

使用儲存庫中的 `core_schema.sql`、`core_validation_hardening.sql`、`core_chat.sql`、`core_chat_variable_scope.sql`，加上僅供測試的 Auth 相容表／函式。建立兩個 NOLOGIN 測試角色，以及3位合成使用者、3筆合成 session、3筆個人資料、25筆拼單、2筆已接受申請、1則聊天訊息。另有1筆序號／觸發器稽核資料。

1. 使用 `pg_dump --format=custom` 產生真正的 PostgreSQL archive，不只是 JSON 或 TEMP 表。
2. 移除來源容器，再建立全新空白目標。檔案 SHA-256／16MiB大小檢查通過才允許還原。
3. 刻意缺少必要角色，確認 `pg_restore --exit-on-error --single-transaction` 失敗，且所有應用 schema 都回滾。
4. 明確建立已知測試角色後還原，逐筆比對全部合成資料，以及資料表 ACL／RLS policies／索引／約束／函式／觸發器／序號狀態與測試角色旗標。PostgreSQL 的 NULL ACL 與等價預設 ACL 正規化後比較，並實際檢查權限。
5. 以 restricted role 與合成 session 執行本人資訊及聊天室 RPC；確認匿名讀取及一般使用者直接修改貼文均被拒絕。另驗證 CHECK、外鍵與觸發器仍有效。
6. 確認非空白目標被拒絕、修改過的 archive 被拒絕。最後移除容器，不 prune 其他 Docker 資源。

備份、checksum manifest 與本輪結果留在 ignored `.local/postgres-backups/<隨機批次>/`；不提交 Git、不搬入映像。失敗診斷在 `.local/postgres-rehearsal-failure.log`，可能含合成 SQL 錯誤，公開前仍須檢閱。沒有對外還原入口，也沒有自動套用未知 archive。

## 還不能代表什麼

這是已知 **core/chat 基礎 schema** 的隔離邏輯備份演練，不是目前 Supabase 全部 migration、偷揪／向量資料、Storage 圖檔、Google OAuth、密碼／真實 session 或正式角色備份。Auth 相容函式只模擬資料庫呼叫身分，沒有取代真正 JWT／GoTrue 驗證。測試角色由固定安全 scaffold 重建，沒有備份全站角色或密碼。

目前驗證相同 PostgreSQL17 的邏輯還原；未涵蓋跨主版本、WAL/PITR、真實事故 RPO/RTO、Mac ARM64 或所有現行功能在災難復原後的端到端驗收。未來正式還原應另建空白專案並完整準備 Auth／Storage／設定與秘密，不能直接拿本工具覆蓋現有 Supabase。

參考：[PostgreSQL pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html)、[pg_restore 的角色與交易限制](https://www.postgresql.org/docs/17/app-pgrestore.html)。
