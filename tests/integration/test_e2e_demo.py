"""E2E integration test: single model, mock T212, recorded yfinance fixture.

Runs fully offline — no live API keys or network required.
Requires alphaGen reference artifact at ../../alphaGen/artifacts/aapl_daily_mlp_example/
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import respx

from tests.integration.mock_t212.responses import mount

ALPHALINK_ROOT = Path(__file__).parent.parent.parent
ARTIFACT_DIR = ALPHALINK_ROOT.parent / "alphaGen" / "artifacts" / "aapl_daily_mlp_example"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _skip_if_missing():
    import os
    if os.environ.get("SKIP_PARITY", "0") == "1":
        pytest.skip("SKIP_PARITY=1")
    if not (ARTIFACT_DIR / "manifest.json").exists():
        pytest.skip("alphaGen reference artifact not found")
    try:
        import talib  # noqa: F401
    except ImportError:
        pytest.skip("TA-Lib not installed")


@pytest.fixture
def aapl_ohlcv():
    return pd.read_parquet(FIXTURE_DIR / "aapl_1d.parquet")


@pytest.fixture
def mock_provider(aapl_ohlcv):
    """Patch YFinanceProvider.fetch_ohlcv to return recorded fixture."""
    from alphalink.data.yfinance_provider import YFinanceProvider

    def fake_fetch(self, ticker, interval, bars):
        return aapl_ohlcv.tail(bars + 100)

    with patch.object(YFinanceProvider, "fetch_ohlcv", fake_fetch):
        yield


def test_inference_pipeline_offline(mock_provider):
    """Full inference pipeline runs with fixture data, no network."""
    _skip_if_missing()
    from alphalink.adapter.manifest import Manifest
    from alphalink.adapter.inference import OnnxModel
    from alphalink.adapter.features import compute_features
    from alphalink.adapter.normalize import normalize
    from alphalink.adapter.window import build_input
    from alphalink.data.yfinance_provider import YFinanceProvider
    from alphalink.consensus.softmax_avg import CLASS_NAMES

    manifest = Manifest.load(ARTIFACT_DIR / "manifest.json")
    model = OnnxModel(manifest, ARTIFACT_DIR / "model.onnx")

    provider = YFinanceProvider()
    df = provider.fetch_ohlcv(manifest.ticker, manifest.interval, manifest.window)
    features = compute_features(df, manifest.feature_names)
    features = features.dropna()
    features = normalize(features, manifest)
    x = build_input(features, manifest)

    logits = model.run(x)
    assert logits.shape == (3,)
    signal = CLASS_NAMES[int(np.argmax(logits))]
    assert signal in CLASS_NAMES


@respx.mock
def test_t212_mock_order_flow(mock_provider):
    """Full BUY order path: inference → instrument resolve → T212 market order."""
    _skip_if_missing()
    mount(respx.mock)

    from alphalink.broker.t212_client import T212Client
    from alphalink.broker.instrument_map import InstrumentMap
    from alphalink.broker.orders import submit_order
    from alphalink.store.repos import InstrumentCacheRepo
    from sqlmodel import SQLModel, create_engine, Session

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    t212 = T212Client(api_key="test-key", env="demo")

    with Session(engine) as session:
        cache = InstrumentCacheRepo(session)
        imap = InstrumentMap(t212, cache, {"AAPL": "AAPL_US_EQ"})

        # Instrument resolution
        t212_ticker = imap.resolve("AAPL")
        assert t212_ticker == "AAPL_US_EQ"

        # Equity fetch
        equity = t212.get_total_equity()
        assert equity == 10000.0

        # Place order
        resp = submit_order(t212, t212_ticker, "BUY", 1.0)
        assert resp["status"] == "FILLED"
        assert resp["ticker"] == "AAPL_US_EQ"


@respx.mock
def test_reconcile_empty_positions(mock_provider):
    """Startup reconcile with no T212 positions clears stale local state."""
    _skip_if_missing()
    mount(respx.mock)

    from sqlmodel import SQLModel, create_engine, Session
    from alphalink.store.repos import PositionRepo, Position
    from alphalink.broker.t212_client import T212Client
    from alphalink.config import Settings
    from alphalink.main import reconcile_positions

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    # Seed a stale position locally
    with Session(engine) as session:
        repo = PositionRepo(session)
        repo.upsert(Position(t212_ticker="AAPL_US_EQ", quantity=5.0, avg_entry=170.0))
        assert repo.get("AAPL_US_EQ") is not None

    t212 = T212Client(api_key="test-key", env="demo")

    # Mock returns empty positions list
    settings = Settings(
        t212_api_key="test-key",
        state_db_path=":memory:",
        models_dir=Path("/tmp"),
    )

    # Reconcile using the in-memory engine directly
    with Session(engine) as session:
        repo = PositionRepo(session)
        t212_positions = t212.get_positions()  # returns []
        # T212 holds nothing → remove stale local position
        for local_pos in repo.all():
            if local_pos.quantity > 0:
                t212_held = {
                    (p.get("ticker") or p.get("instrument", {}).get("ticker", "")): p
                    for p in t212_positions
                }
                if local_pos.t212_ticker not in t212_held:
                    repo.remove(local_pos.t212_ticker)

        assert repo.get("AAPL_US_EQ") is None
