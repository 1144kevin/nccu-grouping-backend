"""Start the original login/grouping prototype on loopback, with no cloud services."""

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ["ENVIRONMENT"] = "local"
    os.environ["CORE_CLOUD_ENABLED"] = "false"
    os.environ["CORE_LOCAL_ENABLED"] = "true"
    os.environ["CORE_LOCAL_DB_PATH"] = ".local/core.sqlite3"
    os.environ["API_V1_PREFIX"] = "/api/v1"
    os.environ["BACKEND_CORS_ORIGINS"] = '["http://localhost:8081","http://127.0.0.1:8081"]'
    os.environ["DEBUG"] = "false"
    print("Local accounts / SQLite only. No email, cloud database, or AI calls.")
    print("API: http://127.0.0.1:8000/api/v1 ; Ctrl+C stops the server.")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, proxy_headers=False)


if __name__ == "__main__":
    main()
