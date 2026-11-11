from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.core_cloud.openai_budget import LocalBudget


def test_persists_and_fails_closed_after_limit(tmp_path):
    budget = LocalBudget(tmp_path / "budget.db", lifetime_usd=0.03, daily_usd=0.03)
    budget.reserve(10000, "text")
    LocalBudget(tmp_path / "budget.db", lifetime_usd=0.03, daily_usd=0.03).reserve(20000, "image")
    with pytest.raises(HTTPException) as error:
        budget.reserve(1, "speech")
    assert error.value.detail == "openai_budget_exhausted"


def test_concurrent_reservations_do_not_exceed_cap(tmp_path):
    budget = LocalBudget(tmp_path / "budget.db", lifetime_usd=0.05, daily_usd=0.05)

    def reserve(_):
        try:
            budget.reserve(10000, "text")
            return True
        except HTTPException:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(reserve, range(12))) == 5
