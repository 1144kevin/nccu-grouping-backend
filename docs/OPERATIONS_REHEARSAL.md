# Supabase 效能、容器與還原演練（2026-10-20）

本機程式 + 唯一指定的 Supabase 開發專案 `axdwbfhrkbovgrbngrrh`。沒有部署、付費模型、寄信、推送遠端或覆寫開發資料庫。

## 實際完成程度

| 項目 | 實際結果 |
|---|---|
| Supabase 大量資料 | 實際新增1,000／5,000筆隔離拼單並經本機 API 量測；測後清理 |
| 持續負載 | 約120秒、120個 API 請求、目標1 RPS、最多兩筆在途，全部成功 |
| Supabase 最後名額 | 兩個不同隔離申請同時接受，一個200、一個409，joined=2，未超收 |
| 合成應用資料備份還原 | 匯出至本機、SHA-256核對、刪除來源後還原至獨立 PostgreSQL TEMP 表，逐筆與外鍵檢查成功 |
| Linux 容器 | 新增可直接執行的建置／離線驗收腳本；此機無 Docker／可用 WSL，**沒有實際建出 Linux image** |
| 完整 Supabase 災難復原 | **未完成**。本輪沒有匯出／還原 Auth 密碼、Storage 物件、資料庫角色或整套 Supabase 環境 |

## 實際量測

路徑：Windows loopback FastAPI → Supabase Auth 驗證 → Supabase PostgreSQL RPC → 本機。
數字包含網路、每次身分驗證及回傳資料的成本，不能當成純 SQL 執行時間、iPhone 延遲或正式雲端 API 的吞吐量。

| 情境 | 貼文 p50／p95（ms） | 推薦 p50／p95（ms） | 歷史搜尋 p50／p95（ms） |
|---|---:|---:|---:|
| 新增資料前 | 471.67／1482.95 | 451.86／929.24 | 477.28／1478.78 |
| 新增1,000筆 | 479.17／584.68 | 510.75／749.49 | 467.82／502.58 |
| 新增5,000筆短測 | 493.68／789.03 | 671.21／829.16 | 531.48／774.31 |
| 5,000筆下持續負載 | 494.37／706.77 | 656.77／866.40 | 488.44／642.11 |

短測每條路徑10筆，另有各一筆暖機不計入；nearest-rank p95 在10筆樣本時等於最大值，樣本不足以證明穩定性提升。持續測試每條路徑40筆，共120筆，實測119.55秒，沒有錯誤或觸發停止門檻；最慢請求約1.51秒。

原資料另有127筆示範拼單。短測的貼文使用獨特 fixture 關鍵字，因此新增前該查詢為空；早期歷史搜尋也可能為空。持續測試前將500筆 fixture 設為取消，歷史搜尋改用 fixture 主揪帳號，以涵蓋有結果的歷史資料。初輪腳本亦修正標籤長度及聊天室 URL；持續負載使用符合輸入長度的標籤。這些分佈差異與網路抖動不能解讀成嚴格的線性成長比較。

這是有上限的開發負載演練，不是最大承載能力證明；沒有進行高 RPS、數十人同時登入、長時段 soak、模型壓力或突破免費方案額度的測試。原有模型單程序 FIFO 規則維持不變。

## 隔離與清理

使用3個隨機 scope 的無真人信箱帳號，僅透過一般 Auth 登入取得本人 session；沒有 service-role 金鑰。大量資料的作者只有 fixture 帳號。測試帳號封鎖現有非 fixture 帳號，避免既有使用者看到或參與資料；封鎖列也隨 fixture 清理。測試期間若新增其他帳號，須重新檢查隔離名單。

清理 SQL 先核對精確 UID 與管理端 test scope，若有外部參與者或聊天發送者會拒絕刪除；不掃描刪除其他測試批次。回復後：**127筆拼單、243則訊息，fixture 帳號與 fixture 拼單均為0**。本機臨時帳密及含帳密的 provisioning SQL 已刪除，合成備份與無憑證的量測報告保留在 ignored `.local`。

完整離線後端回歸494項通過，保留兩項既有依賴 deprecation warnings。App 程式未修改。

## 重跑有上限的 Supabase 演練

在後端目錄，Windows 使用 `.venv\Scripts\python.exe`，Mac 使用 `.venv/bin/python`。

1. 啟動既有本機 Supabase API；確認測試期間不新增其他帳號。
2. `python -m scripts.rehearse_supabase_load prepare`：只寫 ignored `.local/supabase-load` 的私有帳密及 SQL，**不自動操作雲端**。
3. 由有權限的 Codex／MCP 檢閱並執行 `prepare.sql`，不要把含測試帳密的內容貼進聊天或 Git。
4. `python -m scripts.rehearse_supabase_load measure --stage baseline`。
5. MCP 執行 `seed-1000.sql`，量測 `measure --stage 1000`；再執行 `seed-5000.sql`，量測 `measure --stage 5000`。新腳本預設10%取消，與初輪短測分佈有差異。
6. `python -m scripts.rehearse_supabase_load last-seat`：真實兩連線搶名額，並建立合成聊天室訊息。
7. `python -m scripts.rehearse_supabase_load run --stage sustained --seconds 120 --rps 1`。
8. 無論是否成功，都檢閱並執行同一批次 `cleanup.sql`，核對 fixture=0；清掉 `accounts.json` 和 `prepare.sql`。失敗或有外部參與者時先停查，不得放寬清理 guard。

固定只連 `127.0.0.1:8000`。最長180秒、最多2 RPS／兩筆在途；不建立無界請求佇列。出現3個錯誤或近期請求超過10秒會提前停止，HTTP逾時15秒。不得為了產生漂亮結果關掉停止門檻。

## 本輪備份還原涵蓋什麼

只擷取 scope 已驗證的3個合成個人資料、27筆拼單、4筆加入申請、1則訊息，不含其他使用者資料。備份有格式版本與 canonical JSON SHA-256。工具檢查大小、scope、作者與子表關聯；檔案被修改或關聯跨出 fixture 即拒絕準備還原。

實際在 Supabase PostgreSQL 建立 session-local TEMP 表，重建 primary／unique／check 及明確外鍵後依序載入。比對全部欄位、測試不存在父列會失敗，最後 ROLLBACK。來源 fixture 刪除後也重跑成功；現有應用表沒有被還原覆蓋。

工具：

```text
python -m scripts.rehearse_backup pack --source .local/supabase-load/graph.json --output .local/backups/synthetic-graph.json
python -m scripts.rehearse_backup restore-sql --source .local/backups/synthetic-graph.json --output .local/backups/restore.sql
```

這兩個指令只處理本機合成檔與產生 SQL。MCP 檢閱後才執行 `restore.sql`；SQL 只寫 TEMP 表，最後 rollback，payload 以 base64 當資料解碼，不執行備份中的文字。它依賴目前資料庫的 table type，**不是可從零重建 schema 的 pg_dump，也不能拿來恢復 Google 登入或圖片**。

[Supabase 備份文件](https://supabase.com/docs/guides/platform/backups) 說明 Free 專案應自行匯出；資料庫備份不包含 Storage 物件。正式災難復原仍需獨立的空白測試環境、完整 schema／roles／Auth 資料、Storage 實體檔案、設定與秘密的安全保管，以及登入／RLS／圖片／刪除資料不復活的驗收。目前沒有這項完成證據或正式 RPO／RTO。

## Linux 容器：交給已有本機 Docker 的環境

```text
python -m scripts.rehearse_container
python -m scripts.rehearse_container --build
```

第一個只檢查；第二個才建置並測試目前 Dockerfile。只接受 default context 的本機 socket 與 Linux engine，拒絕 TCP／SSH／遠端 daemon。用 Buildx default builder 載入本機，不 push；runtime 使用非 root UID10001、唯讀根檔案系統、移除 capabilities、限制 CPU／記憶體、無網路、無 host mount、無發布埠。以假公開設定驗證 import、health 200 與未登入貼文401，再實際啟動預設服務、等待 HEALTHCHECK healthy、驗證 SIGTERM exit0，最後清掉本輪唯一命名的 image／container，不執行 prune 或刪除 volumes。

多階段 image 沿用依賴 builder 與最小 runtime 的做法，參考 [Docker 官方說明](https://docs.docker.com/build/building/multi-stage/)。Dockerfile 與 `.dockerignore` 不含 `.local`、Git、帳密或測試備份。

**後續更新：已依授權安裝 WSL2／Docker，Linux amd64 image 實際建置與離線驗收通過。** 不需先重開機；原生依賴載入、非root／唯讀限制、服務啟動、健康檢查與 SIGTERM 關閉均通過。完整後端498項回歸通過。細節見 [Windows 安裝與重跑](WINDOWS_CONTAINER_SETUP.md)、[本輪結果](evidence/linux-container-20261020.json)。前段494項為先前量測當時的回歸紀錄。

後續已完成 [映像弱點檢查](IMAGE_VULNERABILITY_SCAN.md) 與 [本機 PostgreSQL 隔離還原](POSTGRES_RESTORE_REHEARSAL.md)。Critical 已修至0；仍有44筆 Debian High 命中，無可用修補版本，發布門檻維持 blocked。25筆合成拼單的真正 custom dump 已在來源移除後還原到空白容器，RLS／RPC／約束與觸發器驗收通過。完整後端506項測試通過。

仍待：Mac M1 ARM64 image／實機驗證、剩餘基底弱點處理、完整獨立 Supabase Auth／Storage 還原、較長持續負載與正式容量評估。沒有部署或使用付費服務。
