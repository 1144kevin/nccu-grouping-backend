# 政缺一咖 Backend

FastAPI backend，目前串接登入、拼單、文字聊天與偷揪雲端分身／需求／配對。本機 Qwen3.5 模型已支援自然語言理解及補問；手機推播仍屬後續工作。

新增「偷揪」本機原型：請見 [功能與兩週計畫](docs/STEALTH_PLAN.md)、[本機驗證方式](docs/STEALTH_LOCAL.md) 與 [工作紀錄](docs/STEALTH_PROGRESS.md)。模組預設關閉，沒有取代既有功能。

離線 SQLite 模式也保留信箱／密碼登入、主揪審核、退出釋出名額及前端串接。需要離線開發時可依 [SQLite 本機啟動與測試](docs/CORE_LOCAL.md) 操作；此獨立模式不連雲端、不寄信。延續目前進度請使用下方 Supabase 開發專案模式。

新增 [本機程式 + Supabase 開發專案模式](docs/CORE_SUPABASE_DEV.md)：Google／信箱登入、憑證續期、核心拼單、過往紀錄、文字聊天室、已讀／未讀、申請動態及公開個人資料已串接。指定開發專案保留七筆示範拼單與三則示範訊息；程式在本機執行，沒有部署或啟用付費 AI。離線 SQLite 與原展示模式仍可獨立使用。

偷揪入口已加入 App 選單：同一個 Supabase 帳號建立分身，用免費本機 Qwen3.5 4B 整理需求並補問缺漏，確認後才儲存需求／資源／行程、取得二至三人配對。費用與志願分工仍由規則規劃器提出可檢查草案；大家可修改後逐人確認，全員同意同一版方案才在交易內建立核心拼單與聊天室。人工調整已知價格需填原因，且不能超過共同預算；改版清除舊同意。預設停用；可自行允許示範資源，但示範人物不能同意或成團。手動表單與免費關鍵字提示仍保留。步驟與資料表見上方開發說明。

## 技術與後續規劃

- FastAPI + Pydantic
- SQLAlchemy async + asyncpg、Alembic：既有資料層與部署規劃；目前 `core_*` 雲端流程使用 Supabase 交易 RPC
- Supabase PostgreSQL + Auth；核心寫入使用受限交易 RPC
- SQLite：離線開發與獨立偷揪原型
- Storage、Realtime、pgvector、AI 雲端呼叫與手機推播屬後續整合項目

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

