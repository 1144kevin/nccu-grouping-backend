# 政缺一咖 Backend

FastAPI backend，負責拼單業務規則、PostgreSQL 資料存取、Supabase 整合、AI 推薦與推播。

## 技術

- FastAPI + Pydantic
- SQLAlchemy async + asyncpg
- Alembic migrations
- Supabase PostgreSQL + pgvector
- Supabase Auth、Storage、Realtime
- OpenAI API

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

建立 Supabase project 並設定 `DATABASE_URL` 後：

```bash
uv run alembic revision --autogenerate -m "initial schema"
uv run alembic upgrade head
```

不要直接在正式資料庫手動改 schema；所有變更都必須留下 Alembic migration。

