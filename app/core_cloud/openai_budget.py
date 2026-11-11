"""Persistent, conservative request reservations for the local OpenAI prototype."""

import asyncio
import os
import sqlite3
import ssl
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from pydantic import SecretStr

from app.core.cloud_security import WindowQuota

_rate = WindowQuota()
DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / ".local" / "openai-budget.sqlite3"


def check_rate(kind: str):
    if not _rate.allow("openai_all", 20) or not _rate.allow(("openai", kind), 8):
        raise HTTPException(429, "openai_rate_limit")


class LocalBudget:
    def __init__(self, path: Path, *, lifetime_usd=None, daily_usd=None):
        self.path = Path(path)
        lifetime_usd = (
            lifetime_usd
            if lifetime_usd is not None
            else float(os.environ.get("OPENAI_LOCAL_TOTAL_USD", "50"))
        )
        daily_usd = (
            daily_usd
            if daily_usd is not None
            else float(os.environ.get("OPENAI_LOCAL_DAILY_USD", "10"))
        )
        self.lifetime_units = int(lifetime_usd * 1_000_000)
        self.daily_units = int(daily_usd * 1_000_000)
        if not 0 < self.daily_units <= self.lifetime_units:
            raise ValueError("invalid_budget")

    def reserve(self, units: int, kind: str):
        if (
            not isinstance(units, int)
            or units <= 0
            or kind not in ("text", "image", "embedding", "speech")
        ):
            raise ValueError("invalid_reservation")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as db:
                db.execute("PRAGMA busy_timeout=5000")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY, "
                    "day TEXT NOT NULL, kind TEXT NOT NULL, units INTEGER NOT NULL)"
                )
                db.execute("BEGIN IMMEDIATE")
                day = datetime.now(UTC).date().isoformat()
                total, daily = db.execute(
                    "SELECT coalesce(sum(units),0), "
                    "coalesce(sum(CASE WHEN day=? THEN units ELSE 0 END),0) FROM reservations",
                    (day,),
                ).fetchone()
                if total + units > self.lifetime_units or daily + units > self.daily_units:
                    db.execute("ROLLBACK")
                    raise HTTPException(429, "openai_budget_exhausted")
                db.execute(
                    "INSERT INTO reservations(day,kind,units) VALUES(?,?,?)", (day, kind, units)
                )
                db.execute("COMMIT")
        except sqlite3.Error:
            raise HTTPException(503, "openai_budget_unavailable") from None


class PostgresBudget:
    """Staging adapter; call from a synchronous worker, never expose the DSN."""

    def __init__(self, dsn: SecretStr):
        self._dsn = dsn

    def reserve(self, units: int, kind: str):
        if (
            not isinstance(units, int)
            or units <= 0
            or kind not in ("text", "image", "embedding", "speech")
        ):
            raise ValueError("invalid_reservation")

        async def reserve_once():
            import asyncpg

            conn = await asyncpg.connect(
                self._dsn.get_secret_value(), timeout=5, ssl=ssl.create_default_context()
            )
            try:
                return await conn.fetchval(
                    "select ai_budget_private.reserve($1, $2)", kind, units, timeout=5
                )
            finally:
                await conn.close(timeout=5)

        try:
            permitted = asyncio.run(reserve_once())
        except Exception:
            raise HTTPException(503, "openai_budget_unavailable") from None
        if permitted is not True:
            raise HTTPException(429, "openai_budget_exhausted")


def configured_budget(settings):
    if settings.core_cloud_enabled or settings.environment == "cloud":
        if not settings.openai_budget_database_url.get_secret_value().strip():
            raise HTTPException(503, "openai_budget_unavailable")
        return PostgresBudget(settings.openai_budget_database_url)
    return LocalBudget(DEFAULT_LEDGER)
