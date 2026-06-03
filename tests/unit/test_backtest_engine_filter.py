from __future__ import annotations
from unittest.mock import MagicMock, patch
from pathlib import Path
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.config import BacktestConfig


def _make_engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


@patch("alphaTrade.backtest.engine.scan_models")
@patch("alphaTrade.backtest.engine.YFinanceProvider")
def test_model_filter_runs_only_matching_model(mock_provider_cls, mock_scan, tmp_path):
    manifest_a = MagicMock()
    manifest_a.run_name = "AAPL_v1"; manifest_a.ticker = "AAPL"; manifest_a.interval = "1h"; manifest_a.window = 10  # noqa: E702
    manifest_b = MagicMock()
    manifest_b.run_name = "MSFT_v1"; manifest_b.ticker = "MSFT"; manifest_b.interval = "1h"; manifest_b.window = 10  # noqa: E702
    mock_scan.return_value = [(manifest_a, MagicMock()), (manifest_b, MagicMock())]
    mock_provider_cls.return_value.fetch_ohlcv_range.return_value = None  # causes early return

    engine = _make_engine(tmp_path)
    with Session(engine) as s:
        from alphaTrade.backtest.engine import run_backtest
        run_backtest(s, Path("models"), "2025-01-01", "2025-01-31", BacktestConfig(), model_filter="AAPL_v1")

    calls = mock_provider_cls.return_value.fetch_ohlcv_range.call_args_list
    assert "AAPL" in str(calls)
    assert "MSFT" not in str(calls)


@patch("alphaTrade.backtest.engine.scan_models")
@patch("alphaTrade.backtest.engine.YFinanceProvider")
def test_run_id_reused_when_provided(mock_provider_cls, mock_scan, tmp_path):
    mock_scan.return_value = []
    engine = _make_engine(tmp_path)
    with Session(engine) as s:
        from alphaTrade.store.repos import BacktestRepo
        existing_id = BacktestRepo(s).create_run("2025-01-01", "2025-01-31", status="queued")

    with Session(engine) as s:
        from alphaTrade.backtest.engine import run_backtest
        # model_filter with no matching models would normally raise, but mock_scan returns []
        # and we pass model_filter=None so it hits the "no models" path
        try:
            run_backtest(s, Path("models"), "2025-01-01", "2025-01-31", BacktestConfig(), run_id=existing_id)
        except RuntimeError:
            pass  # expected: no models found

    # Confirm no second run was created
    with Session(engine) as s:
        from alphaTrade.store.repos import BacktestRepo
        runs = BacktestRepo(s).list_runs()
    assert len(runs) == 1
    assert runs[0].id == existing_id
