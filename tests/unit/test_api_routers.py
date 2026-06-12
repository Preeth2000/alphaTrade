import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine, health_state=None):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, health_state or HealthState()))


# --- Positions ---

def test_positions_empty(tmp_path):
    client = _client(_engine(tmp_path))
    resp = client.get("/api/v1/positions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_positions_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import Position, PositionRepo
    from datetime import datetime
    with Session(engine) as s:
        PositionRepo(s).upsert(Position(
            t212_ticker="AAPL_US_EQ", quantity=10.0, avg_entry=150.0,
            opened_at=datetime(2026, 1, 1),
        ))
    resp = _client(engine).get("/api/v1/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["t212_ticker"] == "AAPL_US_EQ"


# --- Health ---

def test_health_returns_state(tmp_path):
    state = HealthState()
    state.t212_ok = True
    state.models_loaded = True
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["t212_ok"] is True
    assert body["models_loaded"] is True
    assert body["last_tick_at"] is None


def _ready_state() -> HealthState:
    """Return a HealthState that would be trading_ready if provider_data_ok allows."""
    state = HealthState()
    state.t212_ok = True
    state.t212_configured = True
    state.models_loaded = True
    return state


def test_trading_ready_false_when_provider_data_ok_false(tmp_path):
    state = _ready_state()
    state.provider_data_ok = False
    state.provider_data_error = "no OHLCV bars returned"
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    body = resp.json()
    assert body["trading_ready"] is False
    assert body["provider_data_ok"] is False
    assert body["provider_data_error"] == "no OHLCV bars returned"


def test_trading_ready_true_when_provider_data_ok_none(tmp_path):
    # provider_data_ok=None means unknown — should not block trading
    state = _ready_state()
    state.provider_data_ok = None
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    body = resp.json()
    assert body["trading_ready"] is True
    assert "provider_data_ok" not in body


def test_trading_ready_true_when_provider_data_ok_true(tmp_path):
    state = _ready_state()
    state.provider_data_ok = True
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    body = resp.json()
    assert body["trading_ready"] is True
    assert body["provider_data_ok"] is True


def test_health_emits_provider_data_error_only_when_set(tmp_path):
    state = _ready_state()
    state.provider_data_ok = True
    state.provider_data_error = None
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    body = resp.json()
    assert "provider_data_error" not in body


def test_trading_ready_still_requires_t212_ok(tmp_path):
    # provider_data_ok=True but t212_ok=False → not ready
    state = _ready_state()
    state.t212_ok = False
    state.provider_data_ok = True
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    assert resp.json()["trading_ready"] is False


# --- Orders ---

from datetime import datetime  # noqa: E402


def test_orders_empty_defaults_24h(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/orders")
    assert resp.status_code == 200
    assert resp.json() == []


def test_orders_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import Order
    with Session(engine) as s:
        s.add(Order(ts=datetime(2020, 1, 1), t212_ticker="OLD", side="BUY", quantity=1.0, status="filled"))
        s.add(Order(ts=datetime(2026, 1, 1), t212_ticker="NEW", side="BUY", quantity=1.0, status="filled"))
        s.commit()
    resp = _client(engine).get("/api/v1/orders?since=2025-01-01T00:00:00&limit=50")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["t212_ticker"] == "NEW"


# --- Signals ---

def test_signals_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import Signal
    with Session(engine) as s:
        s.add(Signal(ts=datetime(2020, 1, 1), run_name="r", ticker="OLD", signal="BUY"))
        s.add(Signal(ts=datetime(2026, 1, 1), run_name="r", ticker="NEW", signal="SELL"))
        s.commit()
    resp = _client(engine).get("/api/v1/signals?since=2025-01-01T00:00:00")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "NEW"


# --- PnL ---

def test_pnl_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import PnlSnapshot, PnlSnapshotRepo
    with Session(engine) as s:
        repo = PnlSnapshotRepo(s)
        repo.upsert(PnlSnapshot(date="2020-01-01", total_equity=10000, day_pnl=0, day_pnl_pct=0, realized_pnl=0, unrealized_pnl=0))
        repo.upsert(PnlSnapshot(date="2026-01-01", total_equity=12000, day_pnl=200, day_pnl_pct=1.7, realized_pnl=200, unrealized_pnl=0))
    resp = _client(engine).get("/api/v1/pnl?since=2025-01-01")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["date"] == "2026-01-01"


# --- Models ---

def test_models_empty(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/models")
    assert resp.status_code == 200
    assert resp.json() == []


def test_models_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import ModelPerformanceRepo
    with Session(engine) as s:
        ModelPerformanceRepo(s).get_or_create("my_model")
    resp = _client(engine).get("/api/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["run_name"] == "my_model"


# --- Backtest ---

def test_backtest_runs_empty(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/backtest/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_backtest_trades_404_unknown_run(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/backtest/runs/999/trades")
    assert resp.status_code == 404


def test_backtest_trades_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import BacktestRepo
    with Session(engine) as s:
        run_id = BacktestRepo(s).create_run("2025-01-01", "2025-12-31")
        BacktestRepo(s).record_trade(
            run_id=run_id, model_id="m", side="BUY",
            entry_time=datetime(2025, 1, 1), exit_time=datetime(2025, 2, 1),
            entry_price=100.0, exit_price=110.0, quantity=1.0,
            exit_reason="OCO_TP", realized_pnl=10.0,
        )
    resp = _client(engine).get(f"/api/v1/backtest/runs/{run_id}/trades")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# --- Settings ---

def test_settings_get_returns_defaults(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["t212_active_account"] == "demo"
    assert body["max_positions"] == 5


def test_settings_sensitive_fields_masked(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import BotSettings, BotSettingsRepo
    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, t212_demo_api_key="real-key", alphaTrade_api_key="api-key"))
    resp = _client(engine).get("/api/v1/settings", headers={"X-API-Key": "api-key"})
    body = resp.json()
    assert body["t212_demo_api_key"] == "***"
    assert body["alphaTrade_api_key"] == "***"


def test_settings_put_partial_update(tmp_path):
    engine = _engine(tmp_path)
    client = _client(engine)
    resp = client.put("/api/v1/settings", json={"max_positions": 10, "t212_active_account": "invest"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_positions"] == 10
    assert body["t212_active_account"] == "invest"
    assert body["size_pct"] == pytest.approx(0.10)


def test_settings_put_sensitive_masked_in_response(tmp_path):
    resp = _client(_engine(tmp_path)).put("/api/v1/settings", json={"t212_demo_api_key": "new-key"})
    assert resp.json()["t212_demo_api_key"] == "***"


def test_settings_t212_multi_account(tmp_path):
    c = _client(_engine(tmp_path))
    resp = c.put("/api/v1/settings", json={
        "t212_active_account": "invest",
        "t212_invest_api_key": "invest-key",
        "t212_invest_secret_key": "invest-secret",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["t212_active_account"] == "invest"
    assert body["t212_invest_api_key"] == "***"
    assert body["t212_invest_secret_key"] == "***"


def test_settings_t212_demo_key_masked(tmp_path):
    c = _client(_engine(tmp_path))
    resp = c.put("/api/v1/settings", json={"t212_demo_api_key": "demo-key"})
    assert resp.status_code == 200
    assert resp.json()["t212_demo_api_key"] == "***"


# --- Route completeness ---

def test_create_app_has_all_routes(tmp_path):
    engine = _engine(tmp_path)
    state = HealthState()
    from alphaTrade.api.app import create_app
    app = create_app(engine, state)
    paths = {route.path for route in app.routes}
    assert "/api/v1/positions" in paths
    assert "/api/v1/orders" in paths
    assert "/api/v1/signals" in paths
    assert "/api/v1/pnl" in paths
    assert "/api/v1/models" in paths
    assert "/api/v1/backtest/runs" in paths
    assert "/api/v1/backtest/runs/{run_id}/trades" in paths
    assert "/api/v1/health" in paths
    assert "/api/v1/settings" in paths
    assert "/api/v1/trades" in paths
    assert "/api/v1/equity-curve" in paths
    assert "/api/v1/backtest/runs/{run_id}" in paths
    assert "/api/v1/stream" in paths
    assert "/api/v1/kill-switch" in paths
    assert "/api/v1/halt" in paths
    assert "/api/v1/resume" in paths


# --- Kill switch ---

def test_kill_switch_status_not_halted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _client(_engine(tmp_path)).get("/api/v1/kill-switch")
    assert resp.status_code == 200
    assert resp.json()["halted"] is False


def test_halt_endpoint_creates_sentinel(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _client(_engine(tmp_path)).post("/api/v1/halt")
    assert resp.status_code == 200
    body = resp.json()
    assert body["halted"] is True
    assert (tmp_path / "HALT").exists()


def test_resume_endpoint_removes_sentinel(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "HALT").touch()
    resp = _client(_engine(tmp_path)).post("/api/v1/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body["halted"] is False
    assert not (tmp_path / "HALT").exists()


def test_halt_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client(_engine(tmp_path))
    client.post("/api/v1/halt")
    resp = client.post("/api/v1/halt")
    assert resp.status_code == 200
    assert resp.json()["halted"] is True


def test_resume_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _client(_engine(tmp_path)).post("/api/v1/resume")
    assert resp.status_code == 200
    assert resp.json()["halted"] is False


def test_kill_switch_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client(_engine(tmp_path))
    assert client.get("/api/v1/kill-switch").json()["halted"] is False
    client.post("/api/v1/halt")
    assert client.get("/api/v1/kill-switch").json()["halted"] is True
    client.post("/api/v1/resume")
    assert client.get("/api/v1/kill-switch").json()["halted"] is False


# --- Trades ---

def test_trades_empty(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/trades")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trades_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import TradeJournal, TradeJournalRepo
    with Session(engine) as s:
        repo = TradeJournalRepo(s)
        repo.save(TradeJournal(
            ts=datetime(2020, 1, 1), model_id="m1", ticker="AAPL",
            entry_price=100.0, exit_price=110.0, quantity=1.0,
            entry_time=datetime(2020, 1, 1), exit_time=datetime(2020, 1, 2),
            exit_reason="SIGNAL_SELL", realized_pnl=10.0, pnl_pct=0.10,
        ))
        repo.save(TradeJournal(
            ts=datetime(2026, 1, 1), model_id="m1", ticker="TSLA",
            entry_price=200.0, exit_price=220.0, quantity=2.0,
            entry_time=datetime(2026, 1, 1), exit_time=datetime(2026, 1, 2),
            exit_reason="OCO_TP", realized_pnl=40.0, pnl_pct=0.10,
        ))
    resp = _client(engine).get("/api/v1/trades?since=2025-01-01T00:00:00")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "TSLA"


def test_trades_model_id_filter(tmp_path):
    engine = _engine(tmp_path)
    from alphaTrade.store.repos import TradeJournal, TradeJournalRepo
    with Session(engine) as s:
        repo = TradeJournalRepo(s)
        for ticker, model in [("AAPL", "model_a"), ("TSLA", "model_b")]:
            repo.save(TradeJournal(
                ts=datetime(2026, 1, 1), model_id=model, ticker=ticker,
                entry_price=100.0, exit_price=110.0, quantity=1.0,
                entry_time=datetime(2026, 1, 1), exit_time=datetime(2026, 1, 2),
                exit_reason="SIGNAL_SELL", realized_pnl=10.0, pnl_pct=0.10,
            ))
    resp = _client(engine).get("/api/v1/trades?model_id=model_a")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["model_id"] == "model_a"


# --- CORS ---

def test_cors_preflight_returns_allow_origin(tmp_path):
    from fastapi.testclient import TestClient
    from alphaTrade.api.app import create_app
    client = TestClient(create_app(_engine(tmp_path), HealthState()))
    resp = client.options(
        "/api/v1/positions",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_cors_get_response_has_allow_origin(tmp_path):
    from fastapi.testclient import TestClient
    from alphaTrade.api.app import create_app
    client = TestClient(create_app(_engine(tmp_path), HealthState()))
    resp = client.get("/api/v1/positions", headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"
