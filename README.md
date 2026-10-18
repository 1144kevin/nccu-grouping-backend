# 政缺一咖 Backend

推薦 v2 已依授權套用至指定 Supabase 開發專案，保留新增 120 篇展示資料，現在共 127 篇。隔離帳號已透過本機 API 驗證排序、理由、名額及游標，測後清理；App／API 不部署。詳見 [開發說明](docs/CORE_SUPABASE_DEV.md#推薦-v2開發專案已套用)。

FastAPI backend，目前串接登入、拼單、文字聊天與偷揪雲端分身／需求／配對。本機 Qwen3.5 模型已支援自然語言理解及補問；手機推播仍屬後續工作。

已新增可替換的需求理解、向量與候選判讀接口，以及免費本機 Qwen 語義排序。App 偷揪結果頁已接上手動語義排序與判讀，顯示理由及待確認事項。啟用方式、兩種資料模式的差異與評估案例見 [本機語義配對](docs/CORE_SUPABASE_DEV.md#本機語義配對與可替換-ai-接口)。Apple／OpenAI adapter 與雲端向量索引尚未整合；原有配對及全員確認流程保留。

原 App 新增雲端個人偏好、規則推薦排序、個人聊天室封存／靜音、沒興趣／截止提醒、私人回報與分類搜尋。詳細操作與界線見 [Supabase 開發說明](docs/CORE_SUPABASE_DEV.md)；iPhone Google 回跳、離線 release 檢查與 Mac 接續步驟見 [iPhone／TestFlight](docs/IPHONE_TESTFLIGHT.md)。API 仍只在本機執行。

新增「偷揪」本機原型：請見 [功能與兩週計畫](docs/STEALTH_PLAN.md)、[本機驗證方式](docs/STEALTH_LOCAL.md) 與 [工作紀錄](docs/STEALTH_PROGRESS.md)。模組預設關閉，沒有取代既有功能。

離線 SQLite 模式也保留信箱／密碼登入、主揪審核、退出釋出名額及前端串接。需要離線開發時可依 [SQLite 本機啟動與測試](docs/CORE_LOCAL.md) 操作；此獨立模式不連雲端、不寄信。延續目前進度請使用下方 Supabase 開發專案模式。

新增 [本機程式 + Supabase 開發專案模式](docs/CORE_SUPABASE_DEV.md)：Google／信箱登入、憑證續期、核心拼單、過往紀錄、文字聊天室、已讀／未讀、申請動態及公開個人資料已串接。指定開發專案保留七筆示範拼單與三則示範訊息；程式在本機執行，沒有部署或啟用付費 AI。離線 SQLite 與原展示模式仍可獨立使用。

偷揪入口已加入 App 選單：同一個 Supabase 帳號建立分身，用免費本機 Qwen3.5 4B 整理需求並補問缺漏，確認後才儲存需求／資源／行程、取得二至三人配對。費用與志願分工仍由規則規劃器提出可檢查草案；大家可修改後逐人確認，全員同意同一版方案才在交易內建立核心拼單與聊天室。人工調整已知價格需填原因，且不能超過共同預算；改版清除舊同意。預設停用；可自行允許示範資源，但示範人物不能同意或成團。手動表單與免費關鍵字提示仍保留。步驟與資料表見上方開發說明。

## 技術與後續規劃

本輪已完成項目、仍缺功能及 Mac／iPhone 驗收表整理於 [開發交付與發行準備](docs/RELEASE_READINESS.md)。改動依功能在原分支分開 commit；沒有 push，TestFlight 尚未建置或上傳。

- FastAPI + Pydantic
- SQLAlchemy async + asyncpg、Alembic：既有資料層與部署規劃；目前 `core_*` 雲端流程使用 Supabase 交易 RPC
- Supabase PostgreSQL + Auth；核心寫入使用受限交易 RPC
- SQLite：離線開發與獨立偷揪原型
- Supabase 私人 Storage：單張拼單附圖，後端縮圖／移除 EXIF，再驗證發布
- Realtime、pgvector、AI 雲端呼叫與手機推播屬後續整合項目

## 本機啟動

需要 Python 3.12 或 3.13，建議使用 `uv`：

```bash
cp .env.example .env
uv sync --dev
uv run uvicorn app.main:app --reload
```

啟動後：

- Health check：`http://localhost:8000/health`
- Swagger UI：`http://localhost:8000/docs`
- OpenAPI JSON：`http://localhost:8000/openapi.json`

## 檢查

```bash
uv run ruff check .
uv run pytest
```

## Migration

Alembic initial schema 是早期規劃；目前 Supabase 的 `core_*` 開發資料表由 `supabase/` 中已記錄的 SQL migrations 建立。**不要直接將 Alembic initial schema 套到目前 Supabase 專案，也不要一次重跑所有 SQL 檔案。** 已套用版本與測試方式見 [開發說明](docs/CORE_SUPABASE_DEV.md)。

若只要查看既有 Alembic 規劃：

```bash
uv run alembic current
uv run alembic history
```

後續結構變更另增 migration 並審查 SQL。本機 SQLite 不需要雲端 migration。本輪僅在已指定的 Supabase 開發專案新增結構與示範資料；沒有部署。


Mac／iPhone 最短交接指令、離線 Git bundle 與三種連線 profile 已整理於 [交接流程](docs/IPHONE_TESTFLIGHT.md)，實機結果請填 [驗收清單](docs/DEVICE_ACCEPTANCE.csv)。Windows 準備不代表已完成原生編譯或部署。

雲端執行入口與部署前檢查已備妥，尚未部署：見 [雲端準備與環境合約](docs/CORE_SUPABASE_DEV.md)。`scripts/run_cloud_core.py` 預設只檢查；不使用本機runner部署。雲端第一版限定單程序／單實例及免費規則草案。
