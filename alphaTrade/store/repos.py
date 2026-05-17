"""ORM models and repos: signals, orders, positions, equity_curve, instruments."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, Session, select


class Signal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    run_name: str
    ticker: str
    signal: str          # BUY | SELL | HOLD
    model_count: int = 1
    raw_json: str = ""   # JSON-encoded logits list for audit


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    signal_id: Optional[int] = None
    t212_ticker: str
    side: str            # BUY | SELL
    quantity: float
    status: str = "pending"  # pending | filled | rejected | error
    t212_order_id: str = ""
    fill_price: Optional[float] = None
    error_msg: str = ""
    client_order_id: str = Field(default="", index=True)


class Position(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    t212_ticker: str = Field(index=True, unique=True)
    quantity: float
    avg_entry: float
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    last_signal_ts: Optional[datetime] = None
    cooldown_until_ts: Optional[datetime] = None
    stop_order_id: Optional[str] = None
    limit_order_id: Optional[str] = None


class EquityCurve(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    equity: float


class InstrumentCache(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    yf_ticker: str = Field(index=True, unique=True)
    t212_ticker: str
    resolved_at: datetime = Field(default_factory=datetime.utcnow)


class SignalRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def save(self, sig: Signal) -> Signal:
        self._s.add(sig)
        self._s.commit()
        self._s.refresh(sig)
        return sig

    def list(self, limit: int = 100) -> list[Signal]:
        return list(self._s.exec(
            select(Signal).order_by(Signal.ts.desc()).limit(limit)
        ).all())

    def since(self, since: datetime, limit: int = 500) -> list[Signal]:
        return list(self._s.exec(
            select(Signal).where(Signal.ts >= since).order_by(Signal.ts.desc()).limit(limit)
        ).all())


class OrderRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def save(self, order: Order) -> Order:
        self._s.add(order)
        self._s.commit()
        self._s.refresh(order)
        return order

    def find_by_client_order_id(self, cid: str) -> Order | None:
        return self._s.exec(
            select(Order).where(Order.client_order_id == cid)
        ).first()

    def update_fill(self, order_id: int, status: str, fill_price: float | None, t212_id: str) -> None:
        order = self._s.get(Order, order_id)
        if order:
            order.status = status
            order.fill_price = fill_price
            order.t212_order_id = t212_id
            self._s.commit()

    def list(self, limit: int = 100) -> list[Order]:
        return list(self._s.exec(
            select(Order).order_by(Order.ts.desc()).limit(limit)
        ).all())

    def since(self, since: datetime, limit: int = 500) -> list[Order]:
        return list(self._s.exec(
            select(Order).where(Order.ts >= since).order_by(Order.ts.desc()).limit(limit)
        ).all())


class PositionRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, t212_ticker: str) -> Position | None:
        return self._s.exec(
            select(Position).where(Position.t212_ticker == t212_ticker)
        ).first()

    def all(self) -> list[Position]:
        return list(self._s.exec(select(Position)).all())

    def upsert(self, pos: Position) -> None:
        existing = self.get(pos.t212_ticker)
        if existing:
            existing.quantity = pos.quantity
            existing.avg_entry = pos.avg_entry
            existing.last_signal_ts = pos.last_signal_ts
            existing.cooldown_until_ts = pos.cooldown_until_ts
            # Preserve OCO IDs — only overwrite if caller explicitly sets them
            if pos.stop_order_id is not None:
                existing.stop_order_id = pos.stop_order_id
            if pos.limit_order_id is not None:
                existing.limit_order_id = pos.limit_order_id
        else:
            self._s.add(pos)
        self._s.commit()

    def update_oco_ids(self, t212_ticker: str, stop_order_id: str, limit_order_id: str) -> None:
        existing = self.get(t212_ticker)
        if existing:
            existing.stop_order_id = stop_order_id
            existing.limit_order_id = limit_order_id
            self._s.commit()

    def all_with_oco(self) -> list[Position]:
        return list(self._s.exec(
            select(Position).where(
                Position.stop_order_id.isnot(None),
                Position.limit_order_id.isnot(None),
            )
        ).all())

    def remove(self, t212_ticker: str) -> None:
        existing = self.get(t212_ticker)
        if existing:
            self._s.delete(existing)
            self._s.commit()


class EquityRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def record(self, equity: float) -> None:
        self._s.add(EquityCurve(equity=equity))
        self._s.commit()

    def today_open(self) -> float | None:
        today = datetime.utcnow().date()
        rows = list(self._s.exec(select(EquityCurve)).all())
        for row in rows:
            if row.ts.date() == today:
                return row.equity
        return None

    def since(self, since: datetime, limit: int = 500) -> list[EquityCurve]:
        return list(self._s.exec(
            select(EquityCurve).where(EquityCurve.ts >= since).order_by(EquityCurve.ts).limit(limit)
        ).all())


class InstrumentCacheRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, yf_ticker: str) -> InstrumentCache | None:
        return self._s.exec(
            select(InstrumentCache).where(InstrumentCache.yf_ticker == yf_ticker)
        ).first()

    def get_by_t212(self, t212_ticker: str) -> InstrumentCache | None:
        return self._s.exec(
            select(InstrumentCache).where(InstrumentCache.t212_ticker == t212_ticker)
        ).first()

    def put(self, yf_ticker: str, t212_ticker: str) -> None:
        existing = self.get(yf_ticker)
        if existing:
            existing.t212_ticker = t212_ticker
            existing.resolved_at = datetime.utcnow()
        else:
            self._s.add(InstrumentCache(yf_ticker=yf_ticker, t212_ticker=t212_ticker))
        self._s.commit()


# ---------------------------------------------------------------------------
# New ORM models (migration 0002)
# ---------------------------------------------------------------------------

class TradeJournal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    model_id: str = Field(index=True)
    ticker: str = Field(index=True)
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    hold_bars: int = 0
    exit_reason: str  # OCO_SL | OCO_TP | SIGNAL_SELL | HALT | MANUAL
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    realized_pnl: float
    pnl_pct: float


class PnlSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(unique=True, index=True)  # YYYY-MM-DD
    total_equity: float
    day_pnl: float
    day_pnl_pct: float
    realized_pnl: float
    unrealized_pnl: float
    positions_json: str = "{}"
    open_positions: int = 0
    trade_count: int = 0


class ModelPerformance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: str = Field(unique=True, index=True)
    trade_count: int = 0
    win_count: int = 0
    rolling_pnl: float = 0.0
    rolling_trades_json: str = "[]"  # JSON list of last N realized_pnl values
    retired: bool = False
    retired_at: Optional[datetime] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class SectorCache(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    yf_ticker: str = Field(unique=True, index=True)
    sector: str
    resolved_at: datetime = Field(default_factory=datetime.utcnow)


class BacktestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    start_date: str
    end_date: str
    config_json: str = "{}"
    status: str = "done"


class BacktestTrade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    model_id: str
    side: str
    entry_bar: int = 0
    exit_bar: int = 0
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    exit_reason: str
    realized_pnl: float
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None


# ---------------------------------------------------------------------------
# New repo classes (migration 0002)
# ---------------------------------------------------------------------------

class TradeJournalRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def save(self, entry: TradeJournal) -> TradeJournal:
        self._s.add(entry)
        self._s.commit()
        self._s.refresh(entry)
        return entry

    def by_model(self, model_id: str, limit: int = 100) -> list[TradeJournal]:
        return list(self._s.exec(
            select(TradeJournal)
            .where(TradeJournal.model_id == model_id)
            .order_by(TradeJournal.ts.desc())
            .limit(limit)
        ).all())

    def today(self) -> list[TradeJournal]:
        today = datetime.utcnow().date()
        return list(self._s.exec(
            select(TradeJournal).where(TradeJournal.ts >= datetime(today.year, today.month, today.day))
        ).all())

    def since(self, since: datetime | str) -> list[TradeJournal]:
        if isinstance(since, str):
            since = datetime.fromisoformat(since)
        return list(self._s.exec(
            select(TradeJournal).where(TradeJournal.ts >= since)
        ).all())


class PnlSnapshotRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(self, snap: PnlSnapshot) -> None:
        existing = self._s.exec(
            select(PnlSnapshot).where(PnlSnapshot.date == snap.date)
        ).first()
        if existing:
            existing.total_equity = snap.total_equity
            existing.day_pnl = snap.day_pnl
            existing.day_pnl_pct = snap.day_pnl_pct
            existing.realized_pnl = snap.realized_pnl
            existing.unrealized_pnl = snap.unrealized_pnl
            existing.positions_json = snap.positions_json
            existing.open_positions = snap.open_positions
            existing.trade_count = snap.trade_count
        else:
            self._s.add(snap)
        self._s.commit()

    def since(self, since: str) -> list[PnlSnapshot]:
        return list(self._s.exec(
            select(PnlSnapshot).where(PnlSnapshot.date >= since).order_by(PnlSnapshot.date)
        ).all())


class ModelPerformanceRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_or_create(self, model_id: str) -> ModelPerformance:
        existing = self._s.exec(
            select(ModelPerformance).where(ModelPerformance.model_id == model_id)
        ).first()
        if existing:
            return existing
        new = ModelPerformance(model_id=model_id, last_updated=datetime.utcnow())
        self._s.add(new)
        self._s.commit()
        self._s.refresh(new)
        return new

    def update(self, perf: ModelPerformance) -> None:
        perf.last_updated = datetime.utcnow()
        self._s.add(perf)
        self._s.commit()

    def is_retired(self, model_id: str) -> bool:
        row = self._s.exec(
            select(ModelPerformance).where(ModelPerformance.model_id == model_id)
        ).first()
        return row.retired if row else False

    def all(self) -> list[ModelPerformance]:
        return list(self._s.exec(select(ModelPerformance)).all())


class SectorCacheRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, yf_ticker: str) -> SectorCache | None:
        return self._s.exec(
            select(SectorCache).where(SectorCache.yf_ticker == yf_ticker)
        ).first()

    def put(self, yf_ticker: str, sector: str) -> None:
        existing = self.get(yf_ticker)
        if existing:
            existing.sector = sector
            existing.resolved_at = datetime.utcnow()
        else:
            self._s.add(SectorCache(yf_ticker=yf_ticker, sector=sector))
        self._s.commit()


class BacktestRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create_run(self, start: str, end: str, config_json: str = "{}", status: str = "done") -> int:
        """Create a new BacktestRun and return its id."""
        run = BacktestRun(start_date=start, end_date=end, config_json=config_json, status=status)
        self._s.add(run)
        self._s.commit()
        self._s.refresh(run)
        return run.id

    def update_status(self, run_id: int, status: str) -> None:
        run = self._s.get(BacktestRun, run_id)
        if run:
            run.status = status
            self._s.commit()

    def record_trade(self, run_id: int, **kwargs) -> None:
        """Save a BacktestTrade row. kwargs maps to BacktestTrade fields."""
        trade = BacktestTrade(run_id=run_id, **kwargs)
        self._s.add(trade)
        self._s.commit()

    def trades_for_run(self, run_id: int) -> list[BacktestTrade]:
        return list(self._s.exec(
            select(BacktestTrade).where(BacktestTrade.run_id == run_id)
        ).all())

    def list_runs(self, limit: int = 50) -> list[BacktestRun]:
        return list(self._s.exec(
            select(BacktestRun).order_by(BacktestRun.ts.desc()).limit(limit)
        ).all())


# ---------------------------------------------------------------------------
# BotSettings (migration 0003)
# ---------------------------------------------------------------------------

class BotSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    t212_active_account: str = Field(default="demo")
    t212_demo_api_key: str = Field(default="")
    t212_demo_secret_key: str = Field(default="")
    t212_invest_api_key: str = Field(default="")
    t212_invest_secret_key: str = Field(default="")
    t212_isa_api_key: str = Field(default="")
    t212_isa_secret_key: str = Field(default="")
    data_provider: str = Field(default="yfinance")
    polygon_api_key: str = Field(default="")
    slack_enabled: bool = Field(default=False)
    slack_webhook_url: str = Field(default="")
    slack_min_level: str = Field(default="WARNING")
    email_enabled: bool = Field(default=False)
    email_smtp_host: str = Field(default="")
    email_smtp_port: int = Field(default=587)
    email_smtp_user: str = Field(default="")
    email_smtp_password: str = Field(default="")
    email_from_addr: str = Field(default="")
    email_to_addrs: str = Field(default="")
    email_min_level: str = Field(default="WARNING")
    size_pct: float = Field(default=0.10)
    stop_loss_pct: float = Field(default=0.02)
    take_profit_pct: float = Field(default=0.05)
    cooldown_bars: int = Field(default=3)
    extended_hours: bool = Field(default=False)
    max_positions: int = Field(default=5)
    daily_loss_halt_pct: float = Field(default=0.05)
    alphaTrade_api_key: str = Field(default="")


class BotSettingsRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self) -> BotSettings | None:
        return self._s.get(BotSettings, 1)

    def upsert(self, settings: BotSettings) -> None:
        settings.id = 1
        existing = self._s.get(BotSettings, 1)
        if existing:
            for key, val in settings.model_dump(exclude={"id"}).items():
                setattr(existing, key, val)
        else:
            self._s.add(settings)
        self._s.commit()


# ---------------------------------------------------------------------------
# ModelOverrideRecord (migration 0006)
# ---------------------------------------------------------------------------

class ModelOverrideRecord(SQLModel, table=True):
    __tablename__ = "model_override"

    run_name: str = Field(primary_key=True)
    enabled: Optional[bool] = None
    broker_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ModelOverrideRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, run_name: str) -> ModelOverrideRecord | None:
        return self._s.get(ModelOverrideRecord, run_name)

    def all(self) -> list[ModelOverrideRecord]:
        return list(self._s.exec(select(ModelOverrideRecord)).all())

    def upsert(self, record: ModelOverrideRecord) -> ModelOverrideRecord:
        record.updated_at = datetime.utcnow()
        existing = self.get(record.run_name)
        if existing:
            for field, val in record.model_dump(exclude={"run_name"}).items():
                setattr(existing, field, val)
            self._s.commit()
            self._s.refresh(existing)
            return existing
        self._s.add(record)
        self._s.commit()
        self._s.refresh(record)
        return record

    def delete(self, run_name: str) -> bool:
        existing = self.get(run_name)
        if existing:
            self._s.delete(existing)
            self._s.commit()
            return True
        return False
