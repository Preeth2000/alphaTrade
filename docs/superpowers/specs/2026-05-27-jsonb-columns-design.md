# JSONB Column Migration Design

**Date:** 2026-05-27  
**Issue:** alphatrade-bhz  
**Status:** Approved

## Context

5 columns store JSON as plain `TEXT`/`String`. Postgres is now the primary DB (empty). Converting to native `JSONB` eliminates app-layer serialization boilerplate and enables in-DB JSON querying.

## Columns in Scope

| Column | Table | Model | Current type | New type |
|---|---|---|---|---|
| `raw_json` | `signal` | `Signal` | `str` | `list` |
| `positions_json` | `pnlsnapshot` | `PnlSnapshot` | `str` | `dict` |
| `rolling_trades_json` | `modelperformance` | `ModelPerformance` | `str` | `list` |
| `config_json` | `backtestrun` | `BacktestRun` | `str` | `dict` |
| `unbalanced_sector_overrides` | `botsettings` | `BotSettings` | `str \| None` | `dict \| None` |

## Migration

New Alembic migration `0015_jsonb_columns.py`:

```python
op.alter_column('signal', 'raw_json', type_=postgresql.JSONB, postgresql_using='raw_json::jsonb')
op.alter_column('pnlsnapshot', 'positions_json', type_=postgresql.JSONB, postgresql_using='positions_json::jsonb')
op.alter_column('modelperformance', 'rolling_trades_json', type_=postgresql.JSONB, postgresql_using='rolling_trades_json::jsonb')
op.alter_column('backtestrun', 'config_json', type_=postgresql.JSONB, postgresql_using='config_json::jsonb')
op.alter_column('botsettings', 'unbalanced_sector_overrides', type_=postgresql.JSONB, postgresql_using='unbalanced_sector_overrides::jsonb', nullable=True)
```

`USING` clause handles future migration against populated DB. DB is currently empty so no cast failures expected.

## Model Changes (`store/repos.py`)

- Field types change from `str` to `dict` / `list`
- `sa_column=Column(JSONB)` added explicitly
- Remove `_validate_sector_overrides_json` Pydantic validator — JSONB enforces valid JSON at DB level
- Default values stay equivalent: `{}` → `{}`, `[]` → `[]`

## Call Site Changes

**`risk/performance.py`**  
Remove `json.loads(perf.rolling_trades_json)` and `json.dumps(trades)` — field is now native list.

**`main.py`**  
- `raw_json=json.dumps([l.tolist() …])` → `raw_json=[l.tolist() for l in …]`
- `json.loads(db_s.unbalanced_sector_overrides)` → direct use of `db_s.unbalanced_sector_overrides`

**`backtest/engine.py` + `scheduler/backtest_scheduler.py`**  
`config_json=cfg.model_dump_json()` → `config_json=cfg.model_dump()`

## Tests

Update fixtures and assertions that pass JSON strings for these columns → pass dicts/lists directly.

## Out of Scope

- `label_params` in `adapter/manifest.py` — not a DB column, in-memory only
- Redis stream JSON (`stream_bus.py`, `model_sync.py`) — not DB columns
- CLI output `json.dumps` — output formatting, unrelated
