"""CLI: alphalink run | verify <model_dir> | status"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="alphalink", add_completion=False)
console = Console()


@app.command()
def run(
    overrides: Path = typer.Option(Path("./overrides.yaml"), envvar="OVERRIDES_PATH"),
):
    """Start the trading bot daemon."""
    from alphalink.config import Settings
    from alphalink.main import run as _run

    settings = Settings()
    asyncio.run(_run(settings))


@app.command()
def verify(
    model_dir: Path = typer.Argument(..., help="Path to artifact dir containing manifest.json + model.onnx"),
    ticker_override: str = typer.Option("", "--ticker", help="Override manifest ticker for data fetch"),
):
    """Hash-check, smoke-test, and dry-run inference on a model artifact."""
    import numpy as np
    from alphalink.adapter.manifest import Manifest
    from alphalink.adapter.inference import OnnxModel
    from alphalink.adapter.features import compute_features
    from alphalink.adapter.normalize import normalize
    from alphalink.adapter.window import build_input
    from alphalink.data.yfinance_provider import YFinanceProvider
    from alphalink.consensus.softmax_avg import CLASS_NAMES, _softmax

    manifest_path = model_dir / "manifest.json"
    model_path = model_dir / "model.onnx"

    if not manifest_path.exists():
        console.print(f"[red]manifest.json not found in {model_dir}[/red]")
        raise typer.Exit(1)
    if not model_path.exists():
        console.print(f"[red]model.onnx not found in {model_dir}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Loading manifest...[/bold]")
    manifest = Manifest.load(manifest_path)
    console.print(f"  run_name : {manifest.run_name}")
    console.print(f"  arch     : {manifest.model_arch}")
    console.print(f"  ticker   : {manifest.ticker}")
    console.print(f"  interval : {manifest.interval}")
    console.print(f"  features : {manifest.n_features}")
    console.print(f"  window   : {manifest.window}")

    console.print(f"\n[bold]Hash check...[/bold]")
    manifest.verify_model_hash(model_path)
    console.print("  [green]OK[/green]")

    console.print(f"\n[bold]Loading ONNX model + smoke test...[/bold]")
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

    console.print(f"\n[bold]Running inference...[/bold]")
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
    from alphalink.config import Settings
    from alphalink.store.db import get_session
    from alphalink.store.repos import PositionRepo, EquityRepo
    from alphalink.main import scan_models

    settings = Settings()

    console.print("\n[bold]Loaded models:[/bold]")
    models = scan_models(settings.models_dir)
    for manifest, _ in models:
        console.print(f"  {manifest.run_name}  ({manifest.ticker}, {manifest.interval})")

    with get_session(settings.state_db_path) as session:
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
    from alphalink.kill_switch import SENTINEL_FILE
    Path(SENTINEL_FILE).touch()
    console.print(f"[yellow]Kill switch engaged — {SENTINEL_FILE} created. Bot alive but orders paused.[/yellow]")


@app.command()
def resume():
    """Disengage kill switch: remove HALT sentinel file to resume order submission."""
    from alphalink.kill_switch import SENTINEL_FILE
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
    from alphalink.config import Settings
    from alphalink.backtest.engine import run_backtest
    from alphalink.backtest.reporter import compute_summary, format_text
    from alphalink.store.db import get_session

    settings = Settings()
    mdir = models_dir or settings.models_dir

    console.print(f"[bold]Running backtest[/bold] {start} → {end} from {mdir}")

    with get_session(settings.state_db_path) as session:
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


if __name__ == "__main__":
    app()
