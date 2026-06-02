# Tennis Odds Sources & Setup

## Current Status

| Source | Status | Blocker |
|---|---|---|
| **odds-api.io** | ✅ Integrated (awaiting API key) | Need to register & add `ODDS_API_IO_KEY` to Render |
| The Odds API | ❌ Blocked | HTTP 403 "Host not in allowlist" |

---

## Step 1: Register at odds-api.io (FREE, 2 minutes)

1. Go to **https://odds-api.io/**
2. Click **"Get Free API Key"** or **"Sign Up"**
3. Enter email — no credit card required
4. Copy your API key

**Free tier:** 100 requests/hour, 2 bookmakers per call, ATP/WTA tennis included.
2 scans/day × 30 days = 60 requests/month — well within limits.

---

## Step 2: Add key to Render Dashboard

1. Open **https://render.com/** → your `bet-api` service
2. Go to **Environment** tab
3. Add new variable:
   - **Key:** `ODDS_API_IO_KEY`
   - **Value:** your API key from step 1
4. Click **Save Changes** — service will redeploy automatically

---

## Step 3: Verify (optional)

Check Render logs for:
```
[runtime-odds] tennis odds-api.io: N events cached
```

If you see this, tennis signals will start appearing in the next scan (07:00 or 15:00 UTC).

---

## Architecture

```
get_tennis_h2h_events()          # src/services/runtime_odds.py
    │
    ├─ ODDS_API_IO_KEY set?
    │   YES → odds-api.io         # src/ingest/oddsapiio_tennis.py
    │           ↓ 8h cache
    │           ↓ convert to The Odds API v4 format
    │           ↓ return events
    │
    └─ ODDS_API_IO_KEY not set → The Odds API (blocked by IP allowlist)
```

---

## odds-api.io Response Format

The adapter converts odds-api.io format → The Odds API v4 format automatically.
No changes needed in tennis_signal_scan.py.

**Input (odds-api.io):**
```json
{
  "id": 12345,
  "home": "Novak Djokovic",
  "away": "Carlos Alcaraz",
  "startTime": "2026-06-10T10:00:00Z",
  "bookmakers": [
    {
      "name": "Bet365",
      "markets": [
        {
          "name": "moneyline",
          "outcomes": [
            {"name": "Novak Djokovic", "price": 2.5},
            {"name": "Carlos Alcaraz", "price": 1.6}
          ]
        }
      ]
    }
  ]
}
```

**Output (The Odds API v4 format):**
```json
{
  "id": "12345",
  "sport_key": "tennis_atp",
  "home_team": "Novak Djokovic",
  "away_team": "Carlos Alcaraz",
  "commence_time": "2026-06-10T10:00:00Z",
  "bookmakers": [
    {
      "key": "bet365",
      "title": "Bet365",
      "markets": [
        {
          "key": "h2h",
          "outcomes": [
            {"name": "Novak Djokovic", "price": 2.5},
            {"name": "Carlos Alcaraz", "price": 1.6}
          ]
        }
      ]
    }
  ]
}
```

---

## Bookmaker Configuration

On free tier, max 2 bookmakers per call. Default: `singbet,bet365`.

To change, set in Render Environment:
```
ODDS_API_IO_BOOKMAKERS=singbet,bet365
```

Available bookmakers: check https://odds-api.io/ → bookmakers list.

---

## Evaluated Alternatives (audit summary)

| Source | Tennis | Free | IP Restrictions | Verdict |
|---|---|---|---|---|
| **odds-api.io** | ✅ ATP/WTA | 100 req/hour | ❌ None | **CHOSEN** |
| OddsPapi.io | ✅ ATP/WTA/ITF | 250 req/month | ❌ None | Viable backup |
| BetsAPI | ✅ ATP/WTA | ❌ $10/month | ❌ None | Paid backup |
| The Odds API | ✅ ATP/WTA | 500 req/month | ✅ IP allowlist | Blocked |
| api-sports.io | ❌ No tennis | N/A | — | Rejected |
| Betfair Exchange | ✅ ATP/WTA | With account | ✅ GeoIP | Geo-blocked |
| Sportradar | ✅ All | 30-day trial | ❌ None | Enterprise cost |
| Sofascore | Scores only | Unofficial | ✅ Cloud blocked | Rejected |
| Pinnacle API | ✅ ATP/WTA | ❌ Closed Jul'25 | — | Rejected |

---

## History

| Date | Event |
|---|---|
| 2026-06-02 | The Odds API blocked (HTTP 403 IP allowlist) |
| 2026-06-02 | api-sports.io rejected (no tennis support) |
| 2026-06-02 | Full audit of 10 sources by research agent |
| 2026-06-02 | **odds-api.io selected and integrated** |
| 2026-06-02 | Awaiting `ODDS_API_IO_KEY` in Render Dashboard |

