from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core_cloud.openai_budget import PostgresBudget


@pytest.mark.parametrize("result, expected", [(True, None), (False, 429), (None, 429)])
def test_postgres_budget_honours_transaction_decision(monkeypatch, result, expected):
    calls = []

    class Connection:
        async def fetchval(self, sql, *args, **kwargs):
            calls.append((sql, args))
            return result

        async def close(self, **kwargs):
            calls.append("closed")

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setitem(__import__("sys").modules, "asyncpg", SimpleNamespace(connect=connect))
    budget = PostgresBudget(SecretStr("synthetic"))
    if expected:
        with pytest.raises(HTTPException) as error:
            budget.reserve(50000, "text")
        assert error.value.status_code == expected
    else:
        budget.reserve(50000, "text")
    assert calls[0][1] == ("text", 50000)
    assert calls[1] == "closed"


def test_postgres_budget_fails_closed_without_leaking_error(monkeypatch):
    async def connect(*args, **kwargs):
        raise RuntimeError("synthetic sensitive connection error")

    monkeypatch.setitem(__import__("sys").modules, "asyncpg", SimpleNamespace(connect=connect))
    with pytest.raises(HTTPException) as error:
        PostgresBudget(SecretStr("synthetic")).reserve(1, "text")
    assert error.value.status_code == 503
    assert error.value.detail == "openai_budget_unavailable"
