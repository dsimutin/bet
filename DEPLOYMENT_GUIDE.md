# Deployment Guide — Ready for Render

**Status:** ✅ All code synced, tested, and documented. Ready to deploy.

---

## One-Time Setup (Do This First)

### 1. Get THE_ODDS_API_KEY (CRITICAL)
This is the only blocking requirement.

1. Go to https://the-odds-api.com/
2. Click "Register" and create account
3. Verify email
4. Log in → Account → Copy API Key
5. Save it somewhere safe (you'll paste it to Render)

**Why this is critical:** Without it, signal generation returns 0 candidates.

### 2. Get Telegram Info (Optional but Recommended)

**For bot token:**
1. Open Telegram, search for "@BotFather"
2. Send `/start` then `/newbot`
3. Follow prompts, copy the token (looks like: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

**For chat ID:**
1. Search Telegram for "@userinfobot"
2. Send any message, copy the "Id" number (looks like: `123456789`)

---

## Deploy to Render

### Step 1: Add Environment Variables
In Render Dashboard → Environment:

```
THE_ODDS_API_KEY = (paste from step 1 above) ⭐ REQUIRED
TELEGRAM_BOT_TOKEN = (paste from bot setup, optional)
TELEGRAM_CHAT_ID = (paste from userinfobot, optional)
ACTIVE_MODE = true
```

**How to add:**
1. Go to https://dashboard.render.com
2. Select your service → Environment
3. Click "Add" for each variable
4. Paste values
5. Save and redeploy

### Step 2: Deploy
```bash
git push origin all-the-best
```
(Render will auto-detect and deploy)

### Step 3: Monitor First 24 Hours

**Check logs:**
```
Render Dashboard → Logs
```

**What you should see:**
```
[signals] unified scan started 2026-06-02T07:00:00+00:00
[signals] football EPL: 3 candidate(s)
[signals] tennis: 1 candidate(s)
[signals] exotic: 0 candidate(s)
```

**If you see 0 candidates everywhere:**
- ✅ Check if THE_ODDS_API_KEY is set correctly in environment
- ✅ Check Render logs for error messages
- ✅ Confirm API key is from https://the-odds-api.com (not confuse with other APIs)

**If you see candidates > 0:**
- ✅ System is working! Model will start learning from results
- ✅ Signals appear in web UI and Telegram (if configured)

---

## Branch Status

Both branches are synchronized:
- ✅ `all-the-best` — main production branch
- ✅ `claude/friendly-planck-L2jF7` — development branch (synced)
- ✅ `fix/p0-supabase-runtime` — outdated (ignore)

**What was verified:**
- ✅ 331 unit tests pass (100%)
- ✅ API quota = 417/month (fits in 500 limit with margin)
- ✅ Settlement works for football, tennis, and exotic leagues
- ✅ Demo signals properly expired (won't interfere)
- ✅ All code on main branches

---

## What Each Part Does

### Signal Generation (07:00 and 15:00 UTC)
Scans:
- 5 football leagues (EPL, Bundesliga, LaLiga, Serie A, Ligue 1)
- 2 tennis competitions (ATP, WTA)
- 6 exotic leagues (MLS, Brazil, Argentina, RPL, J-League, Liga MX)

Uses 8-hour cache → only 1 fresh API call per day.

### Settlement
Matches finished games against generated signals:
- Football: Uses historical CSV + live API fallback
- Tennis: Uses Sackmann data + live API fallback
- Exotic: Uses live API only

### Model Learning
Learns from settled signals to adjust thresholds:
- Base models: Train daily on full match history
- Feedback policy: Learns from paper signals to refine priority vs watchlist classification

---

## Quota Monitoring

**Your monthly budget:**
- Free tier: 500 calls
- Scheduled scans: 390 calls
- Admin overhead: 27 calls
- **Available for user requests:** 83 calls

**Staying within budget:**
- ✅ Default configuration (6 exotic leagues): 417 calls/month
- ✅ Can't add extended exotic leagues (8 more): would be 457 calls
- ✅ Have ~3% headroom for user requests

---

## Troubleshooting

### Problem: 0 signals generated
**Check 1:** Is THE_ODDS_API_KEY set?
```bash
# In Render logs, you should see no error about missing API key
# If you see: "no API key — skipping" → set THE_ODDS_API_KEY
```

**Check 2:** Is THE_ODDS_API_KEY valid?
```bash
# Make a test request:
curl "https://api.the-odds-api.com/v4/sports?apiKey=YOUR_KEY_HERE"
# Should return JSON list, not 401 error
```

### Problem: Signals don't match with results
**Expected:** Settlement takes 6-24h (depends on when game finishes)

**What to check:**
- Match has actually finished (not cancelled/postponed)
- Signal was for same teams and league
- Check logs: `[settle] settled N signal(s)`

### Problem: Telegram not receiving alerts
**Check 1:** Is THE_ODDS_API_KEY set? (Need signals first)

**Check 2:** Are bot token and chat ID correct?
```bash
# Test in Render:
curl -X POST https://api.telegram.org/botBOT_TOKEN/sendMessage \
  -d '{"chat_id": "CHAT_ID", "text": "Test"}'
# Should return: "ok":true
```

### Problem: High API quota usage
Check what's consuming quota:
- Scheduled scans: Should be ~13 calls/day
- If seeing 2x that: Cache TTL might be too low
- If extended exotic enabled: That adds 8 calls/day = 240/month

---

## Next Steps After Deploy

1. **Monitor first 24h:** Confirm signals are generated at 07:00 and 15:00 UTC
2. **Wait 3-5 days:** For first signals to settle and populate ledger
3. **Check feedback policy:** Once 20+ signals settled per segment, model calibrates
4. **Enable Telegram:** If not done yet (optional but recommended)

---

## Support

### If something goes wrong:
1. Check Render logs (Render Dashboard → Logs)
2. Verify environment variables are set
3. Test THE_ODDS_API_KEY independently
4. Check RELEASE_AUDIT_2026-06-02.md for detailed troubleshooting

### Common Issues:
- **"signal generation failed"** → Check THE_ODDS_API_KEY
- **"settlement failed"** → Check event has actually finished
- **"no telegram delivery"** → Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

---

**You're ready to deploy! 🚀**

