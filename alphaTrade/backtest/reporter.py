"""Backtest result summarizer and text formatter."""
from __future__ import annotations

from typing import Any


def compute_summary(trades: list[dict], initial_equity: float = 10_000.0) -> dict[str, Any]:
    """Compute summary statistics from a list of trade dicts."""
    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "max_drawdown_pct": 0.0,
            "by_model": {},
        }

    pnls = [t["realized_pnl"] for t in trades]
    total_pnl = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / total

    # Max drawdown
    equity = initial_equity
    peak = equity
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Per-model breakdown
    by_model: dict[str, dict] = {}
    for t in trades:
        mid = t["model_id"]
        if mid not in by_model:
            by_model[mid] = {"trades": 0, "pnl": 0.0, "wins": 0}
        by_model[mid]["trades"] += 1
        by_model[mid]["pnl"] += t["realized_pnl"]
        if t["realized_pnl"] > 0:
            by_model[mid]["wins"] += 1

    return {
        "total_trades": total,
        "total_pnl": round(total_pnl, 4),
        "win_rate": win_rate,
        "max_drawdown_pct": round(max_dd, 3),
        "by_model": by_model,
    }


def format_text(summary: dict[str, Any]) -> str:
    """Format summary dict as human-readable text."""
    lines = [
        f"Total trades   : {summary['total_trades']}",
        f"Total P&L      : {summary.get('total_pnl', 0.0):.4f}",
        f"Win rate       : {summary.get('win_rate', 0.0):.1%}",
        f"Max drawdown   : {summary.get('max_drawdown_pct', 0.0):.3f}%",
    ]
    by_model = summary.get("by_model", {})
    if by_model:
        lines.append("\nBy model:")
        for model_id, stats in by_model.items():
            wr = stats["wins"] / stats["trades"] if stats["trades"] else 0.0
            lines.append(
                f"  {model_id}: {stats['trades']} trades, "
                f"P&L={stats['pnl']:.2f}, WR={wr:.1%}"
            )
    return "\n".join(lines)
