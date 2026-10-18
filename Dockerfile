FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /srv/app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/srv/app/.venv/bin:$PATH" CLOUD_BIND_HOST=0.0.0.0
WORKDIR /srv/app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --from=builder /srv/app/.venv ./.venv
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 scripts/run_cloud_core.py ./scripts/run_cloud_core.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; u='http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health'; urllib.request.build_opener(urllib.request.ProxyHandler({})).open(u,timeout=3).close()"]
CMD ["python", "scripts/run_cloud_core.py", "--serve"]

