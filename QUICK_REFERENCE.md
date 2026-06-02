# Quick Reference — Common Operations

## Pre-Deployment Checklist

```bash
# 1. Run health check (verifies everything is set up)
python scripts/health_check.py

# 2. Run all tests (takes ~60 seconds, must pass 331/331)
python -m pytest tests/ -v

# 3. Check API quota budget
python -c "from src.monitoring.quota_dashboard import quota_report; print(quota_report())"

# 4. Commit and push
git add -A
git commit -m "fix: [description]"
git push origin all-the-best

# 5. Monitor Render logs
# Render dashboard → Logs → watch for [signals] output at 07:00 UTC
```

---

## Common Issues & Solutions

### Issue: "Signal generation returns 0 candidates"

**Check 1: THE_ODDS_API_KEY set?**
```bash
echo $THE_ODDS_API_KEY | wc -c  # Should be > 20 chars
```

**Check 2: API key is valid?**
```bash
curl "https://api.the-odds-api.com/v4/sports?apiKey=$THE_ODDS_API_KEY"
# Should return JSON, not 401 error
```

**Check 3: Cache is working?**
- 07:00 UTC scan → API call (fresh data)
- 15:00 UTC scan → cache hit (no API call)
- If seeing 2 API calls/day → cache TTL may be too short

### Issue: "Telegram alerts not delivering"

**Check:**
```bash
# Test bot token and chat ID
curl -X POST https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage \
  -d '{"chat_id": "'$TELEGRAM_CHAT_ID'", "text": "Test"}'
# Should return: "ok":true
```

**If 401 error:** Token is invalid. Get new one from @BotFather  
**If 400 error:** Chat ID is invalid. Get from @userinfobot

### Issue: "Settlement not happening"

**Check:**
1. Did match actually finish? (Not cancelled/postponed)
2. Check logs: `grep -i settle data/logs/*.log` (if logging enabled)
3. Verify same league/teams in signal and result
4. May take 6-24h for settlement depending on when match finished

### Issue: "API quota usage seems high"

**Check what's consuming quota:**
```bash
python -c "from src.monitoring.quota_dashboard import quota_report; print(quota_report())"
```

**Common causes:**
- Cache TTL too low (should be 28800s = 8h)
- Extended exotic leagues enabled (8 extra leagues = 240 calls/month)
- User-initiated requests (web API calls if implemented)

**Solutions:**
- Verify `ODDS_CACHE_TTL_SECONDS=28800`
- Disable extended exotic leagues: `EXOTIC_LEAGUES=` (empty)
- Check Render dashboard for unexpected API calls

---

## Daily Operations

### Monitor Signal Generation

```bash
# Watch logs in real-time (Render dashboard)
# Expected: 07:00 and 15:00 UTC
# [signals] unified scan started
# [signals] football EPL: 3 candidate(s)
# [signals] tennis: 1 candidate(s)
# [signals] exotic: 0 candidate(s)
```

### Check Current Signal Status

```bash
# Count open signals
python -c "
import json
from pathlib import Path
ledger = json.loads(Path('data/core/paper_signal_ledger.json').read_text())
entries = ledger.get('entries', {})
print(f\"Total: {len(entries)}\")
print(f\"Open: {sum(1 for e in entries.values() if e.get('ledger_status')=='open')}\")
print(f\"Settled: {sum(1 for e in entries.values() if e.get('ledger_status')=='settled')}\")
print(f\"Expired: {sum(1 for e in entries.values() if e.get('ledger_status')=='expired')}\")
"
```

### Check Model Learning Status

```bash
# See how many signals per odds segment
python -c "
import json
from pathlib import Path
ledger = json.loads(Path('data/core/paper_signal_ledger.json').read_text())
entries = ledger.get('entries', {})
settled = [e for e in entries.values() if e.get('ledger_status')=='settled']

# Count by odds segment
segments = {}
for sig in settled:
    odds = float(sig.get('entry_odds', 0))
    if odds < 1.7:
        seg = 'favorite (<1.7)'
    elif odds < 2.5:
        seg = 'balanced (1.7-2.5)'
    elif odds < 4.0:
        seg = 'underdog (2.5-4.0)'
    else:
        seg = 'longshot (>4.0)'
    segments[seg] = segments.get(seg, 0) + 1

for seg in ['favorite (<1.7)', 'balanced (1.7-2.5)', 'underdog (2.5-4.0)', 'longshot (>4.0)']:
    print(f'{seg:25s}: {segments.get(seg, 0):3d} signals')
"
```

### Export Signals for Analysis

```bash
# Get all settled signals in JSON
python -c "
import json
from pathlib import Path
ledger = json.loads(Path('data/core/paper_signal_ledger.json').read_text())
settled = {
    sid: e for sid, e in ledger.get('entries', {}).items()
    if e.get('ledger_status') == 'settled'
}
print(json.dumps(settled, indent=2))
" > settled_signals.json
```

---

## Configuration Changes

### Enable Extended Exotic Leagues

```bash
# Add to Render environment:
EXOTIC_LEAGUES=soccer_vietnam_v_league_1,soccer_thailand_thai_league,soccer_australia_aleague

# Cost: +3 leagues × 30 calls/month = 90 calls
# New total: 417 + 90 = 507 (⚠️ EXCEEDS free tier by 7!)
# Safe maximum: only 2-3 extended leagues
```

### Expand Football Regions

```bash
# Add to Render environment (adds bookmakers):
FOOTBALL_ODDS_REGIONS=eu,us

# Cost: ~2x API calls (one per region)
# New total: 417 * 2 = 834 (❌ WAY OVER)
# NOT recommended in free tier
```

### Disable Auto-Scan

```bash
# Add to Render environment:
ACTIVE_MODE=false

# Then manually trigger via Telegram or web endpoint
# Cost: Manual control of API usage
```

---

## Performance Tuning

### Reduce Scanning Frequency

```bash
# Currently: 07:00 and 15:00 UTC (2 scans/day)
# To 1 scan/day: modify src/services/scheduler.py
# Cost: Halves API usage (208 calls/month)
```

### Reduce Leagues Scanned

```bash
# Default: EPL, Bundesliga, LaLiga, Serie A, Ligue 1 (5)
# To reduce: LEAGUES=EPL,BUNDESLIGA,LALIGA
# Cost: ~3 less calls/scan = 90 calls/month saved
```

---

## Monitoring & Alerts

### Set Up Quota Alerts

Add to Render Alerts:
```
Condition: If logs contain "[quota] CRITICAL"
Action: Notify via email/Slack
```

### Set Up Settlement Alerts

Add to Render Alerts:
```
Condition: If logs do not contain "[settle]" between 06:00-08:00 UTC
Action: Notify (settlement may have failed)
```

---

## Emergency Actions

### Disable Extended Features (if quota high)

```bash
# In Render environment:
EXOTIC_LEAGUES=           # Empty = use defaults (6 safe leagues)
FOOTBALL_ODDS_REGIONS=eu  # Single region
ACTIVE_MODE=true
```

### Manually Reset Quota Count

```bash
# If quota monitor out of sync:
rm data/logs/quota.json
# Tracker will restart fresh next scan
```

### Force Signal Settlement

```bash
# Settle all open signals against results:
python src/models/settle_signal_ledger.py \
  --ledger-path data/core/paper_signal_ledger.json \
  --results-input data/staging/results.csv \
  --output-path data/core/paper_signal_ledger.json
```

---

## Testing Before Deployment

### Test Signal Generation Locally

```bash
# Requires THE_ODDS_API_KEY set locally
export THE_ODDS_API_KEY=your_key
python src/cron/run_signals.py
# Should print: [signals] football EPL: N candidate(s)
```

### Test Telegram Delivery

```bash
export TELEGRAM_BOT_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
python src/cron/run_telegram_test.py
# Should send test message to your chat
```

### Test Settlement

```bash
# Verify settlement works:
python -m pytest tests/test_signal_ledger.py::TestSettlement -v
```

---

## Documentation & References

- **Full Release Audit:** `RELEASE_AUDIT_2026-06-02.md`
- **Deployment Guide:** `DEPLOYMENT_GUIDE.md`
- **Environment Variables:** `.env.example`
- **API Quota Analysis:** See `RELEASE_AUDIT_2026-06-02.md` section 3
- **Model Learning:** See `RELEASE_AUDIT_2026-06-02.md` section 5

---

## Support

- **Quota issues:** Run `python -c "from src.monitoring.quota_dashboard import quota_report; print(quota_report())"`
- **Config validation:** Run `python scripts/health_check.py`
- **Tests:** Run `python -m pytest tests/ -v`
- **Logs:** Check Render dashboard → Logs
