import pytest

from scripts import benchmark_local, competition_demo


def test_benchmark_is_disposable_and_cannot_oversell(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark_local, "ROOT", tmp_path)
    result = benchmark_local.run(posts=10, attempts=3)
    assert result["accepted"] == 1 and result["rejected_full"] == 2
    assert not list((tmp_path / ".local").glob("benchmark-*/"))
    with pytest.raises(ValueError):
        benchmark_local.run(posts=1_000_000)


def test_competition_rehearsal_never_calls_model_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(competition_demo, "ROOT", tmp_path)
    report = competition_demo.run()
    assert report["model_called"] is False
    assert report["members"] == 3 and report["total_cost"] == 240
    assert report["without_consent"] == "blocked"
    assert report["repeated_formation"] == "same_id"
    assert not list((tmp_path / ".local").glob("competition-*/"))
