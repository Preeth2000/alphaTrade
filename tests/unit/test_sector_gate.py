"""Tests for sector exposure gate (balanced and unbalanced modes)."""
from unittest.mock import patch, MagicMock

import pytest
from sqlmodel import Session

from alphalink.config import BalancedPortfolioConfig, UnbalancedPortfolioConfig, RiskConfig
from alphalink.risk.sector import fetch_sector, check_sector_gate
from alphalink.risk.gates import run_gates, GateResult
from alphalink.store.db import get_engine
from alphalink.store.repos import Position, PositionRepo, SectorCacheRepo, ModelPerformanceRepo


@pytest.fixture
def engine(tmp_path):
    import alphalink.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def test_fetch_sector_from_cache(engine):
    with Session(engine) as s:
        SectorCacheRepo(s).put("AAPL", "Technology")
    with Session(engine) as s:
        sector = fetch_sector("AAPL", SectorCacheRepo(s))
    assert sector == "Technology"


def test_fetch_sector_from_yfinance_on_miss(engine):
    mock_ticker = MagicMock()
    mock_ticker.info = {"sector": "Healthcare"}
    with patch("yfinance.Ticker", return_value=mock_ticker):
        with Session(engine) as s:
            sector = fetch_sector("JNJ", SectorCacheRepo(s))
    assert sector == "Healthcare"
    with Session(engine) as s:
        assert SectorCacheRepo(s).get("JNJ").sector == "Healthcare"


def test_fetch_sector_yfinance_failure_returns_unknown(engine):
    with patch("yfinance.Ticker", side_effect=Exception("network error")):
        with Session(engine) as s:
            sector = fetch_sector("FAIL", SectorCacheRepo(s))
    assert sector == "Unknown"


def test_balanced_gate_allows_when_under_limit(engine):
    cfg = BalancedPortfolioConfig(max_sector_pct=0.5)
    with Session(engine) as s:
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL", signal="BUY", equity=10000.0,
            position_repo=PositionRepo(s), sector_repo=sector_repo,
            portfolio_mode="balanced", balanced_cfg=cfg,
            unbalanced_cfg=UnbalancedPortfolioConfig(),
        )
    assert result is None


def test_balanced_gate_blocks_when_over_limit(engine):
    cfg = BalancedPortfolioConfig(max_sector_pct=0.25)
    with Session(engine) as s:
        pos_repo = PositionRepo(s)
        pos_repo.upsert(Position(t212_ticker="MSFT_US_EQ", quantity=10.0, avg_entry=300.0))
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        sector_repo.put("MSFT_US_EQ", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL", signal="BUY", equity=10000.0,
            position_repo=pos_repo, sector_repo=sector_repo,
            portfolio_mode="balanced", balanced_cfg=cfg,
            unbalanced_cfg=UnbalancedPortfolioConfig(),
        )
    assert result is not None
    assert "sector" in result.lower()


def test_unbalanced_gate_allows_under_limit(engine):
    cfg = UnbalancedPortfolioConfig(max_per_sector=3)
    with Session(engine) as s:
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL", signal="BUY", equity=10000.0,
            position_repo=PositionRepo(s), sector_repo=sector_repo,
            portfolio_mode="unbalanced", balanced_cfg=BalancedPortfolioConfig(),
            unbalanced_cfg=cfg,
        )
    assert result is None


def test_unbalanced_gate_blocks_at_limit(engine):
    cfg = UnbalancedPortfolioConfig(max_per_sector=2, sector_overrides={"technology": 2})
    with Session(engine) as s:
        pos_repo = PositionRepo(s)
        pos_repo.upsert(Position(t212_ticker="MSFT_US_EQ", quantity=1.0, avg_entry=300.0))
        pos_repo.upsert(Position(t212_ticker="GOOGL_US_EQ", quantity=1.0, avg_entry=150.0))
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        sector_repo.put("MSFT_US_EQ", "Technology")
        sector_repo.put("GOOGL_US_EQ", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL", signal="BUY", equity=10000.0,
            position_repo=pos_repo, sector_repo=sector_repo,
            portfolio_mode="unbalanced", balanced_cfg=BalancedPortfolioConfig(),
            unbalanced_cfg=cfg,
        )
    assert result is not None


def test_sell_always_passes_sector_gate(engine):
    cfg = BalancedPortfolioConfig(max_sector_pct=0.01)
    with Session(engine) as s:
        result = check_sector_gate(
            yf_ticker="AAPL", signal="SELL", equity=10000.0,
            position_repo=PositionRepo(s), sector_repo=SectorCacheRepo(s),
            portfolio_mode="balanced", balanced_cfg=cfg,
            unbalanced_cfg=UnbalancedPortfolioConfig(),
        )
    assert result is None


def test_gates_blocks_retired_model(engine):
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_z")
        perf.retired = True
        repo.update(perf)

    with Session(engine) as s:
        result = run_gates(
            signal="BUY", t212_ticker="AAPL_US_EQ",
            position_repo=PositionRepo(s), max_positions=10,
            daily_loss_halted=False, model_id="model_z",
            perf_repo=ModelPerformanceRepo(s),
        )
    assert result.approved is False
    assert "retired" in result.reason


def test_gates_blocks_sector_limit(engine):
    cfg = RiskConfig(**{"portfolio_mode": "unbalanced", "unbalanced": {"max_per_sector": 1}})
    with Session(engine) as s:
        pos_repo = PositionRepo(s)
        pos_repo.upsert(Position(t212_ticker="MSFT_US_EQ", quantity=1.0, avg_entry=300.0))
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        sector_repo.put("MSFT_US_EQ", "Technology")
        result = run_gates(
            signal="BUY", t212_ticker="AAPL_US_EQ",
            position_repo=pos_repo, max_positions=10,
            daily_loss_halted=False, yf_ticker="AAPL",
            equity=10000.0, sector_repo=sector_repo, risk_cfg=cfg,
        )
    assert result.approved is False
    assert "sector" in result.reason
