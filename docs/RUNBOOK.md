# Demo-to-Live Promotion Runbook

## 1. Demo Qualification Criteria (2-week minimum)

Before promoting to live, demo run must satisfy ALL:

- [ ] ≥ 14 consecutive calendar days running without crash or unhandled exception
- [ ] ≥ 20 completed tick cycles with orders filled
- [ ] Daily loss halt never triggered (or triggered and recovered correctly)
- [ ] OCO monitor cancelled orphaned legs correctly on every partial fill
- [ ] Kill switch (`alphalink halt` / `alphalink resume`) tested manually
- [ ] No duplicate orders in DB (verify `client_order_id` uniqueness)
- [ ] `alphalink status` shows correct positions matching T212 demo account
- [ ] Webhook notifications firing for fills and halts

## 2. Daily Demo Review Checklist

Run each morning before market open:

```bash
alphalink status                         # positions + PnL
grep -i "error\|exception" alphalink.log | tail -20
grep "Kill switch\|daily loss halt" alphalink.log | tail -10
```

Check:
- [ ] No unhandled exceptions overnight
- [ ] Equity curve trending within expected drawdown
- [ ] No stale positions (open > 5 days without signal update)
- [ ] Log file not exceeding rotation limit

## 3. Pre-Live Config (overrides.yaml)

Reduce sizing and tighten halt threshold before first live tick:

```yaml
defaults:
  size_pct: 0.02          # 2% per position (down from demo 0.10)
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  cooldown_bars: 3
  extended_hours: false

risk:
  max_positions: 3        # fewer concurrent live positions
  daily_loss_halt_pct: 0.02  # halt at 2% daily loss (tighter than demo 0.05)
```

Apply and verify:

```bash
cp overrides.yaml overrides.yaml.demo-backup
# Edit overrides.yaml with values above
alphalink verify <model_dir>             # smoke test each model
```

## 4. Live Cutover Steps

**Warning:** Execute during off-hours (before market open). Do not promote mid-session.

1. Engage kill switch to freeze any running demo bot:
   ```bash
   alphalink halt
   ```

2. Set live environment:
   ```bash
   export T212_ENV=live
   export T212_API_KEY=<live-api-key>   # from T212 live account settings
   ```

3. Verify env (double-check — live orders cost real money):
   ```bash
   echo "ENV: $T212_ENV"
   echo "KEY prefix: ${T212_API_KEY:0:8}..."
   ```

4. Clear kill switch and start:
   ```bash
   alphalink resume
   alphalink run --overrides overrides.yaml
   ```

5. Watch first tick:
   ```bash
   tail -f alphalink.log | grep -E "Filled|Signal|error|Kill switch"
   ```

6. Verify first fill in T212 live account UI matches log output.

## 5. Rollback Plan

If anything looks wrong after cutover:

**Immediate halt (< 30 seconds):**
```bash
alphalink halt                           # freeze orders, bot stays alive
```

**Full rollback:**
```bash
# 1. Stop bot
pkill -f "alphalink run"

# 2. Revert environment
export T212_ENV=demo
export T212_API_KEY=<demo-api-key>

# 3. Restore demo config
cp overrides.yaml.demo-backup overrides.yaml

# 4. Clear kill switch
alphalink resume

# 5. Restart in demo
alphalink run --overrides overrides.yaml
```

**After rollback:** Check T212 live account for any partially-filled orders and cancel manually if open.

## 6. Contacts / Escalation

- T212 API status: https://trading212.com (check announcements)
- Kill switch: `ALPHALINK_HALT=1` env var or `./HALT` sentinel file
