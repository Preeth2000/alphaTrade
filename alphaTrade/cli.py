"""CLI: alphaTrade run | verify <model_dir> | status"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="alphaTrade", add_completion=False)
console = Console()


@app.command()
def run(
    overrides: Path = typer.Option(Path("./overrides.yaml"), envvar="OVERRIDES_PATH"),
):
    """Start the trading bot daemon."""
    from alphaTrade.config import Settings
    from alphaTrade.main import run as _run

    settings = Settings(overrides_path=overrides)
    asyncio.run(_run(settings))


@app.command()
def verify(
    model_dir: Path = typer.Argument(..., help="Path to artifact dir containing manifest.json + model.onnx"),
    ticker_override: str = typer.Option("", "--ticker", help="Override manifest ticker for data fetch"),
):
    """Hash-check, smoke-test, and dry-run inference on a model artifact."""
    from alphaTrade.adapter.manifest import Manifest
    from alphaTrade.adapter.inference import OnnxModel
    from alphaTrade.adapter.features import compute_features
    from alphaTrade.adapter.normalize import normalize
    from alphaTrade.adapter.window import build_input
    from alphaTrade.data.yfinance_provider import YFinanceProvider
    from alphaTrade.consensus.softmax_avg import CLASS_NAMES, _softmax

    manifest_path = model_dir / "manifest.json"
    model_path = model_dir / "model.onnx"

    if not manifest_path.exists():
        console.print(f"[red]manifest.json not found in {model_dir}[/red]")
        raise typer.Exit(1)
    if not model_path.exists():
        console.print(f"[red]model.onnx not found in {model_dir}[/red]")
        raise typer.Exit(1)

    console.print("[bold]Loading manifest...[/bold]")
    manifest = Manifest.load(manifest_path)
    console.print(f"  run_name : {manifest.run_name}")
    console.print(f"  arch     : {manifest.model_arch}")
    console.print(f"  ticker   : {manifest.ticker}")
    console.print(f"  interval : {manifest.interval}")
    console.print(f"  features : {manifest.n_features}")
    console.print(f"  window   : {manifest.window}")

    console.print("\n[bold]Hash check...[/bold]")
    manifest.verify_model_hash(model_path)
    console.print("  [green]OK[/green]")

    console.print("\n[bold]Loading ONNX model + smoke test...[/bold]")
    model = OnnxModel(manifest, model_path)
    console.print("  [green]OK[/green]")

    ticker = ticker_override or manifest.ticker
    console.print(f"\n[bold]Fetching OHLCV ({ticker}, {manifest.interval})...[/bold]")
    provider = YFinanceProvider()
    df = provider.fetch_ohlcv(ticker, manifest.interval, manifest.window)
    console.print(f"  Got {len(df)} bars")

    features = compute_features(df, manifest.feature_names)
    features = features.dropna()
    features = normalize(features, manifest)
    x = build_input(features, manifest)

    console.print("\n[bold]Running inference...[/bold]")
    logits = model.run(x)
    probs = _softmax(logits)
    signal = CLASS_NAMES[int(logits.argmax())]
    confidence = float(probs.max())

    console.print(f"\n  Signal     : [bold {'green' if signal=='BUY' else 'red' if signal=='SELL' else 'yellow'}]{signal}[/bold {'green' if signal=='BUY' else 'red' if signal=='SELL' else 'yellow'}]")
    console.print(f"  Confidence : {confidence:.2%}")
    console.print(f"  Logits     : BUY={logits[0]:.4f}  SELL={logits[1]:.4f}  HOLD={logits[2]:.4f}")
    console.print(f"  Probs      : BUY={probs[0]:.2%}  SELL={probs[1]:.2%}  HOLD={probs[2]:.2%}")


@app.command()
def status():
    """Show loaded models, open positions, and today's PnL from state.db."""
    from alphaTrade.config import Settings
    from alphaTrade.store.db import get_session, url_from_settings
    from alphaTrade.store.repos import PositionRepo, EquityRepo
    from alphaTrade.main import scan_models

    try:
        settings = Settings()
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("Copy [bold].env.example[/bold] to [bold].env[/bold] and set required variables (at minimum T212_API_KEY).")
        raise typer.Exit(1)

    console.print("\n[bold]Loaded models:[/bold]")
    models = scan_models(settings.models_dir)
    for manifest, _ in models:
        console.print(f"  {manifest.run_name}  ({manifest.ticker}, {manifest.interval})")

    with get_session(url_from_settings(settings)) as session:
        positions = PositionRepo(session).all()
        if positions:
            t = Table("ticker", "qty", "avg_entry", "cooldown_until")
            for p in positions:
                cd = p.cooldown_until_ts.isoformat() if p.cooldown_until_ts else "-"
                t.add_row(p.t212_ticker, str(p.quantity), str(p.avg_entry), cd)
            console.print("\n[bold]Open positions:[/bold]")
            console.print(t)
        else:
            console.print("\nNo open positions.")

        today_open = EquityRepo(session).today_open()
        if today_open:
            console.print(f"\n[bold]Day-open equity:[/bold] {today_open:.2f}")


@app.command()
def halt():
    """Engage kill switch: create HALT sentinel file to pause order submission."""
    from alphaTrade.kill_switch import SENTINEL_FILE
    Path(SENTINEL_FILE).touch()
    console.print(f"[yellow]Kill switch engaged — {SENTINEL_FILE} created. Bot alive but orders paused.[/yellow]")


@app.command()
def resume():
    """Disengage kill switch: remove HALT sentinel file to resume order submission."""
    from alphaTrade.kill_switch import SENTINEL_FILE
    p = Path(SENTINEL_FILE)
    if p.exists():
        p.unlink()
        console.print(f"[green]Kill switch cleared — {SENTINEL_FILE} removed. Orders will resume.[/green]")
    else:
        console.print("[green]Kill switch already clear.[/green]")


@app.command()
def backtest(
    start: str = typer.Argument(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Argument(..., help="End date YYYY-MM-DD"),
    models_dir: Path = typer.Option(None, "--models-dir", help="Override default models directory"),
    output: str = typer.Option("text", "--output", "-o", help="Output format: text | json"),
):
    """Run dry-run backtester over historical data for all loaded models."""
    from alphaTrade.config import Settings
    from alphaTrade.backtest.engine import run_backtest
    from alphaTrade.backtest.reporter import compute_summary, format_text
    from alphaTrade.store.db import get_session, url_from_settings

    settings = Settings()
    mdir = models_dir or settings.models_dir

    console.print(f"[bold]Running backtest[/bold] {start} → {end} from {mdir}")

    with get_session(url_from_settings(settings)) as session:
        result = run_backtest(
            session=session,
            models_dir=mdir,
            start=start,
            end=end,
            cfg=settings.backtest,
        )

    trades = result["trades"]
    summary = compute_summary(trades, initial_equity=settings.backtest.initial_equity)

    if output == "json":
        console.print(json.dumps(summary, indent=2))
    else:
        console.print(format_text(summary))


@app.command()
def report(
    since: str = typer.Option("", "--since", help="Start date YYYY-MM-DD (default: 30 days ago)"),
    output: str = typer.Option("text", "--output", "-o", help="Output format: text | json | csv"),
):
    """Print P&L report: daily snapshots and closed trade summary since DATE."""
    import csv
    from datetime import date, timedelta
    from alphaTrade.config import Settings
    from alphaTrade.store.db import get_session, url_from_settings
    from alphaTrade.store.repos import PnlSnapshotRepo, TradeJournalRepo

    try:
        settings = Settings()
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("Copy [bold].env.example[/bold] to [bold].env[/bold] and set required variables (at minimum T212_API_KEY).")
        raise typer.Exit(1)
    since_date = since or (date.today() - timedelta(days=30)).isoformat()

    with get_session(url_from_settings(settings)) as session:
        snapshots = PnlSnapshotRepo(session).since(since_date)
        trades = TradeJournalRepo(session).since(since_date)

    snap_dicts = [
        {
            "date": s.date,
            "total_equity": s.total_equity,
            "day_pnl": s.day_pnl,
            "day_pnl_pct": s.day_pnl_pct,
            "trade_count": s.trade_count,
        }
        for s in snapshots
    ]
    trade_dicts = [
        {
            "ts": t.ts.isoformat(),
            "model_id": t.model_id,
            "ticker": t.ticker,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "quantity": t.quantity,
            "realized_pnl": t.realized_pnl,
            "exit_reason": t.exit_reason,
        }
        for t in trades
    ]

    if output == "json":
        console.print(json.dumps({"snapshots": snap_dicts, "trades": trade_dicts}, indent=2))

    elif output == "csv":
        if trade_dicts:
            writer = csv.DictWriter(sys.stdout, fieldnames=list(trade_dicts[0].keys()))
            writer.writeheader()
            writer.writerows(trade_dicts)
        else:
            console.print("No trades found.")

    else:
        if snap_dicts:
            t = Table("date", "equity", "day P&L", "day %", "trades")
            for s in snap_dicts:
                t.add_row(
                    str(s["date"]),
                    f"{s['total_equity']:.2f}",
                    f"{s['day_pnl']:+.2f}",
                    f"{s['day_pnl_pct']:+.2f}%",
                    str(s["trade_count"]),
                )
            console.print("\n[bold]Daily P&L snapshots:[/bold]")
            console.print(t)
        else:
            console.print(f"No snapshots since {since_date}.")

        total_realized = sum(tr["realized_pnl"] for tr in trade_dicts)  # type: ignore[misc, arg-type]
        console.print(f"\n[bold]Total realized P&L since {since_date}:[/bold] {total_realized:+.2f}")
        console.print(f"[bold]Closed trades:[/bold] {len(trade_dicts)}")


if __name__ == "__main__":
    app()
