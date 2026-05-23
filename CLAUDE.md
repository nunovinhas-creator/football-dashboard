# CLAUDE.md — Football Dashboard

## Project Overview

**Matemática Da Bola** is a fully automated football prediction dashboard. It fetches ML predictions from the BSD API, compares them against Pinnacle odds to detect value bets, generates interactive HTML dashboards, and sends Telegram alerts. Everything runs on a schedule via GitHub Actions and outputs to the `docs/` directory.

**Language:** Python 3.12  
**Dependencies:** `requests` only (no framework)  
**Architecture:** Two standalone scripts, procedural style, file-based state  

---

## Repository Structure

```
football-dashboard/
├── dashboard.py              # Main script: fetch predictions, detect value, generate HTML, send Telegram
├── backtest.py               # Backtesting: snapshot predictions, score results, build/score trebles
├── README.md                 # Portuguese documentation for end users
├── .github/
│   └── workflows/
│       └── dashboard.yml     # Scheduled CI/CD (4× daily)
└── docs/                     # All generated output — committed to git and served via GitHub Pages
    ├── dashboard.html        # Live dashboard (main page, includes today's treble banner)
    ├── backtest.html         # Backtest stats + treble history + ROI
    ├── history.json          # Cumulative backtest records (persistent state)
    ├── trebles.json          # Treble tracking: pending + history + ROI
    ├── preds_YYYY-MM-DD.json # Daily prediction snapshots (includes _pinnacle_odds)
    └── .gitkeep
```

No `src/`, no modules, no classes — all logic lives in the two top-level scripts.

---

## Key Scripts

### dashboard.py

Runs at 07:00, 14:00, and 21:00 UTC. Produces `docs/dashboard.html` and sends Telegram alerts.

**Data flow:**
1. `fetch_all_predictions()` — paginated fetch from BSD API (`limit=50`, offset-based)
2. `enrich(match)` — per-match: fetch single prediction + Pinnacle odds comparison
3. `detect_value(pred, odds)` — flag markets where ML probability > Pinnacle implied + 3%
4. `build_html()` — generate self-contained HTML (inline CSS + JS, dark theme, client-side filters)
5. `send_telegram()` — post value picks as Markdown to Telegram bot

**Required environment variables:**
- `BSD_API_KEY` — BSD API token (`Authorization: Token <key>`)
- `TG_TOKEN` — Telegram bot token
- `TG_CHAT_ID` — Telegram chat ID

### backtest.py

Runs at all four schedule times. Has two modes plus treble construction:

| Mode | When | Action |
|------|------|--------|
| SAVE | 07:00, 14:00, 21:00 UTC | Snapshot today's predictions → `docs/preds_YYYY-MM-DD.json` |
| SCORE | 00:00 UTC (+ always runs) | Fetch results for all pending dates, score predictions, update `history.json`, rebuild `backtest.html` |

**Partial processing:** If < 70% of results are available for a date, it is stored in `dates_partial` and retried the next day.

**Treble construction:** At SAVE time, builds a daily treble from that day's predictions and saves it to `docs/trebles.json`. At SCORE time, resolves pending trebles once the date is fully processed.

**Required environment variables:**
- `BSD_API_KEY`

---

## API Integration

**Base URL:** `https://sports.bzzoiro.com/api/v2`  
**Auth:** `Authorization: Token {BSD_API_KEY}` header  
**Timeout:** 15s (dashboard.py) / 20s (backtest.py)  

Endpoints used:

| Endpoint | Purpose |
|----------|---------|
| `GET /predictions/` | Paginated list of today's predictions |
| `GET /predictions/?event_id=X&limit=1` | Single event prediction lookup |
| `GET /events/{id}/odds/comparison/` | Pinnacle odds comparison |
| `GET /events/{id}/` | Event detail + result status |

The `get(path, params)` helper in both scripts wraps all requests with auth headers and raises on non-2xx responses.

---

## Data Structures

### Prediction record (history.json → `records[]`)
```json
{
  "date": "2026-05-22",
  "league": "Premier League",
  "home": "Arsenal",
  "away": "Chelsea",
  "score": "2-1",
  "prob_home": 0.52,
  "prob_draw": 0.24,
  "prob_away": 0.24,
  "prob_o25": 0.71,
  "prob_btts": 0.58,
  "xg_home": 1.8,
  "xg_away": 1.2,
  "confidence": "ALTA",
  "pred_1x2": "1",
  "actual_1x2": "1",
  "pick_1x2": true,
  "hit_1x2": true,
  "pick_o25": true,
  "hit_o25": true,
  "pick_btts": false,
  "hit_btts": false,
  "pick_xg": false,
  "hit_xg": false
}
```

### history.json root structure
```json
{
  "records": [...],
  "dates_processed": ["2026-05-19", "2026-05-20"],
  "dates_partial": {"2026-05-21": 12}
}
```

---

## Value Detection Logic

```python
# Market is flagged as value if ML edge > 3% over Pinnacle implied probability
edge = ml_probability - pinnacle_implied_probability
if edge > 0.03:
    → VALUE OPPORTUNITY
```

Confidence levels (based on the winning probability):
- **ALTA** — probability ≥ 0.65 (green badge)
- **MÉDIA** — 0.45 ≤ probability < 0.65 (yellow badge)
- **BAIXA** — probability < 0.45 (red badge)

---

## CI/CD: GitHub Actions

**File:** `.github/workflows/dashboard.yml`

Runs on schedule (4× daily) and on manual `workflow_dispatch`. Steps:
1. Checkout → Python 3.12 → `pip install requests`
2. Run `dashboard.py` (with BSD_API_KEY, TG_TOKEN, TG_CHAT_ID)
3. Run `backtest.py` (with BSD_API_KEY)
4. `git add docs/` → commit with timestamp → push

**Commit format:** `update: 2026-05-23T07:00`

**Secrets required in GitHub repo settings:**
- `BSD_API_KEY`
- `TG_TOKEN`
- `TG_CHAT_ID`

---

## Development Conventions

### Language & Comments
- Code uses **English** function/variable names
- Comments and docstrings are in **Portuguese** (project owner's language)
- No type hints anywhere in the codebase — do not add them unless asked

### Code Style
- Procedural: top-level functions called from `main()`, no classes
- Error handling: `try/except` with `print(f"[WARN] ...")` to stdout — no logging framework
- Missing data handled with `None` guards and `–` fallback strings
- f-strings used throughout for HTML/string templating
- Dates/times: always UTC via `datetime.now(timezone.utc)`

### HTML Generation
- Self-contained files: all CSS and JS inlined — no external CDN dependencies
- Dark theme with color-coded confidence levels
- Client-side filtering/sorting via vanilla JavaScript

### State Management
- No database — file I/O only (`docs/history.json`)
- `json.dump(..., ensure_ascii=False, separators=(",", ":"))` for compact UTF-8 JSON

### Pick Thresholds (v3 — data-driven, do not revert)

These thresholds were determined by analysing 5 days of historical data (68 records):

| Market | Threshold | Hit Rate | Rationale |
|--------|-----------|----------|-----------|
| `pick_1x2` | `best ≥ 61% AND conf == "MÉDIA"` | 100% (5/5) | ALTA was 33% — worse than random |
| `pick_btts` | `pb ≥ 61% AND conf IN ("ALTA","MÉDIA")` | 92% (11/12) | BAIXA was 45% — unusable |
| `pick_o25` | `xg_total ≥ 2.9` | (recalibrating) | Model's `po` field was 47% at any threshold |
| `pick_xg` | `xg_total ≥ 2.8` | (informational) | Was "always true" before — 68/68 records |

The `migrate_picks()` function in backtest.py recomputes these for all historical records on every run. Do not remove it — it ensures consistency when thresholds are tuned.

### Treble System

**Selection logic** (`build_daily_treble()`):
1. **Priority 1**: BTTS with ALTA or MÉDIA confidence (92% historical hit rate)
2. **Priority 2**: 1X2 with MÉDIA confidence only (fill when BTTS insufficient)
3. **Max 1 pick per league** — prevents correlated outcomes
4. **Requires ≥ 3 qualifying picks** — no treble generated if insufficient

**Persistence** — `docs/trebles.json`:
```json
{
  "pending": [{"date": "2026-05-23", "picks": [...], "combined_odds": 6.33, "status": "pending"}],
  "history": [{"date": "2026-05-20", "picks": [...], "combined_odds": 5.4, "hit": true, "profit_1u": 4.4}]
}
```

**Scoring** (`score_treble()`): runs in SCORE mode when all 3 picks have real results. Treble is won only if ALL picks hit.

**ROI tracking**: `treble_roi()` computes total staked (1 unit/treble), total returned (combined_odds × wins), and ROI % over all scored trebles.

### Known Bugs Fixed (do not reintroduce)
- `parse_dt()` handles ISO 8601 dates with a `Z` suffix (`s.replace("Z", "+00:00")`)
- `fetch_prediction()` uses the `/predictions/?event_id=X` query param (not a separate endpoint)
- SCORE mode processes **all** unfinished dates, not just yesterday

---

## Running Locally

```bash
# Install dependencies
pip install requests

# Set required env vars
export BSD_API_KEY="your_token"
export TG_TOKEN="your_bot_token"
export TG_CHAT_ID="your_chat_id"

# Run dashboard (generates docs/dashboard.html, sends Telegram)
python dashboard.py

# Run backtest (generates docs/backtest.html, updates docs/history.json)
python backtest.py
```

Output files are written to `docs/` (created automatically if missing).

---

## No Tests

There is no test suite. Changes must be validated by:
1. Running the scripts locally with valid env vars
2. Checking the generated HTML in a browser
3. Monitoring the GitHub Actions workflow run logs

---

## Output / GitHub Pages

The `docs/` directory is committed on every CI run and can be served via GitHub Pages (set source to `docs/` folder on `main` branch). The main page is `docs/dashboard.html`.

All `docs/preds_*.json` files are kept indefinitely as historical snapshots for backtesting.
