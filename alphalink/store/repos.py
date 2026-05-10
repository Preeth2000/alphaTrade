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
        else:
            self._s.add(pos)
        self._s.commit()

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


class InstrumentCacheRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, yf_ticker: str) -> InstrumentCache | None:
        return self._s.exec(
            select(InstrumentCache).where(InstrumentCache.yf_ticker == yf_ticker)
        ).first()

    def put(self, yf_ticker: str, t212_ticker: str) -> None:
        existing = self.get(yf_ticker)
        if existing:
            existing.t212_ticker = t212_ticker
            existing.resolved_at = datetime.utcnow()
        else:
            self._s.add(InstrumentCache(yf_ticker=yf_ticker, t212_ticker=t212_ticker))
        self._s.commit()
