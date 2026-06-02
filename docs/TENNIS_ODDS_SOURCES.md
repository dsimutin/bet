# Tennis Odds Sources & Troubleshooting

## Current Status

**Tennis signals are BLOCKED** due to The Odds API requiring IP allowlist access.

### The Problem
- **THE_ODDS_API_KEY:** `b2f752cc0ee7679fc670c188bc1eb9ff` (set in Render)
- **Error:** HTTP 403 "Host not in allowlist"
- **Scope:** Blocks both ATP and WTA live odds fetch in `get_active_tennis_keys()` → `_fetch_odds()`
- **Impact:** `tennis_signal_scan.py` cannot generate any signals when The Odds API fails

---

## Solution: Contact The Odds API Support

### Steps to Unblock IP Access

1. **Email The Odds API Support:**
   - Visit: https://the-odds-api.com/
   - Look for "Contact" or "Support" link
   - **Request:** "Please remove IP allowlist restrictions from API key `b2f752cc0ee7679fc670c188bc1eb9ff` so it can be accessed from Render.com (IP range: N/A, cloud-based)"
   
2. **Render IP Information:**
   - Render uses dynamic IP ranges
   - Mention in email: "API is deployed on Render.com's frankfurt region (free tier)"
   - Ask if they can allow **all IPs** or provide **Render's IP range**

3. **Timeline:**
   - Expected response: 24-48 hours
   - Once unblocked, no code changes needed—system will work immediately

---

## Rejected Alternatives

### ❌ api-sports.io
- **Status:** No tennis support
- **Supported:** Football, basketball, baseball, hockey only
- **Tested:** Confirmed no tennis endpoints available

### ❌ ESPNbet / BetFair
- **Why rejected:** Require account creation + rate limits too restrictive for free tier
- **Cost:** Paid API access required

---

## Potential Future Solutions (if The Odds API remains blocked)

### 1. **Pinnacle API** (Professional)
- **Pros:** Most accurate odds, no IP restrictions
- **Cons:** Requires account + approval process (2-3 weeks)
- **Cost:** Free for approved sports betting professionals
- **Effort:** High (re-architecture needed)

### 2. **Tennis Data from Betfair**
- **Pros:** Rich historical + live data
- **Cons:** Requires Betfair account + API key
- **Cost:** Free (with account)
- **Effort:** Medium (Betfair API is well-documented)

### 3. **Official ATP/WTA Rankings + Manual Odds Scraping**
- **Pros:** No API limitations
- **Cons:** Manual, labor-intensive, may violate TOS
- **Cost:** Time only
- **Effort:** Very high, not recommended

---

## Current Workaround

**In `src/services/runtime_odds.py` (lines 60-96):**

```python
def get_tennis_h2h_events(api_key: str) -> list[dict[str, Any]]:
    # Returns empty list [] when The Odds API is unavailable
    # Tennis signals simply won't be generated
    # Football signals continue normally
    ...
```

**Frontend behavior:**
- `src/web/today_picks.py` shows "No tennis signals available" message
- System remains operational for football/exotic leagues only

---

## Testing Tennis Recovery

Once The Odds API is unblocked, verify with:

```bash
# 1. Check Render logs for successful tennis fetch
curl https://bet-api-xyz.onrender.com/health

# 2. Run signal scan manually
python -m src.signals.tennis_signal_scan

# 3. Check for generated signals in data/signals/
cat data/signals/2026-06-02_signals.json | jq '.[] | select(.sport == "tennis")'
```

---

## History

| Date | Event | Status |
|---|---|---|
| 2026-06-02 | Initial key tried (`f3b2a2de...`) | 403 IP Allowlist |
| 2026-06-02 | Secondary key issued (`b2f752cc...`) | 403 IP Allowlist |
| 2026-06-02 | api-sports.io fallback implemented | ❌ No tennis support (removed) |
| 2026-06-02 | **Awaiting The Odds API support response** | 🕐 Pending |

---

## Quick Reference

| Component | Status | Blocker |
|---|---|---|
| Football signals | ✅ Working | None |
| Exotic league signals | ✅ Working | None |
| Tennis signals | ❌ Blocked | The Odds API HTTP 403 |
| Tennis settlement | ✅ Ready | Awaiting signals |

