# USOIL Trading Analytics Dashboard — Implementation Plan

## Context

Build a locally-run web dashboard for manual USOIL (WTI Crude) trading using the ICT framework + hidden divergence entries. The tool provides directional bias, key level identification, and setup validation — no automated trading. Data comes from TwelveData API (free tier: 8 req/min, 800/day), rendered with TradingView Lightweight Charts.

**Key technical decisions:**
- TwelveData symbol for WTI crude: `WTI/USD`
- **Lightweight Charts v5** — native multi-pane support for RSI panel (no manual sync needed)
- v5 supports custom overlays via Series Primitives (ISeriesPrimitive with canvas rendering)
- v5 series API: `chart.addSeries(CandlestickSeries, options)` / `chart.addSeries(LineSeries, options, paneIndex)`
- CDN: `https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js`
- **`smartmoneyconcepts` package** for OB/FVG/BOS/CHoCH/liquidity detection — wrap with USOIL-specific tuning
- Aggressive caching is critical given the 800 calls/day limit
- **All value-add features included**: auto-refresh, price alerts, kill zone highlighting, trade notes

**Value-add features beyond the original spec:**
- Data caching with TTL per timeframe (saves API budget)
- Kill zone highlighting on chart (London/NY visual bands)
- Auto-refresh with visibility detection (only refreshes when tab is active)
- Price proximity alerts (toast + audio when price nears key levels)
- Trade idea notes (localStorage-based, no backend needed)

---

## File Structure

```
trading-assistant/
  .env                          # TWELVEDATA_API_KEY
  .env.example                  # Template
  .gitignore                    # .env, __pycache__, .venv
  requirements.txt              # Python dependencies
  README.md                     # Setup instructions
  main.py                       # FastAPI app + routes
  services/
    market_data.py              # TwelveData fetching + caching + rate limiting
    analysis_engine.py          # Orchestrates all analysis modules
  analysis/
    __init__.py
    ict.py                      # OB, FVG, BOS/CHoCH, liquidity, session levels
    divergence.py               # RSI + hidden divergence detection
    bias.py                     # HTF bias scoring engine
    levels.py                   # Key levels aggregation
    checklist.py                # ICT setup checklist
  static/
    index.html                  # Single-page app (HTML + CSS + JS inline)
```

---

## Phase 1: Foundation — Data Layer + Bare Chart
> **PR #1**

**Goal:** FastAPI serving USOIL candles, viewable as a candlestick chart in browser.

### 1a. Project scaffolding
- `requirements.txt`: fastapi, uvicorn[standard], twelvedata, pandas, numpy, ta, smartmoneyconcepts, python-dotenv, scipy
- `.env` / `.env.example`: `TWELVEDATA_API_KEY=your_key_here`
- `.gitignore`: .env, __pycache__/, .venv/, *.pyc

### 1b. `services/market_data.py` — Data fetching + caching
- `get_ohlcv(symbol, interval, outputsize=500) -> pd.DataFrame`
- In-memory cache keyed by interval, with TTL: 15min→2min, 1h→5min, 4h→15min, 1day→60min
- Rate limit tracking: max 7 req/min, max 780/day — return stale cache if limits hit
- `get_multi_tf_data()` — fetches current TF + daily for bias engine

### 1c. `main.py` — FastAPI app (minimal)
- `GET /` → serves static/index.html
- `GET /api/chart-data/{timeframe}` → returns candles JSON (analysis added in Phase 2)
- Mount `/static` directory
- Load API key from .env on startup

### 1d. `static/index.html` — Bare chart skeleton
- TradingView Lightweight Charts v5 from CDN
- Dark theme (#0d1117 background)
- Top bar with symbol label, timeframe buttons (15m/1H/4H/D), refresh button
- Candlestick chart rendering from API data
- Layout: flexbox with chart area (70%) + sidebar placeholder (30%)

**Checkpoint:** Open localhost:8000, see USOIL candles, switch timeframes.

---

## Phase 2: Backend Analysis Engine
> **PR #2**

**Goal:** All ICT analysis computed server-side, returned in unified JSON.

### 2a. `analysis/ict.py`
- `detect_order_blocks(df, last_n=5)` — wraps smartmoneyconcepts OB detection, returns last 5 bullish + 5 bearish unmitigated OBs as {type, top, bottom, start_time, end_time}
- `detect_fvg(df)` — FVG detection, last 10 unmitigated, returns {type, top, bottom, start_time}
- `detect_liquidity(df, tolerance=0.001)` — equal highs/lows within 0.1%, returns {level, type, touches}
- `detect_bos_choch(df)` — BOS and CHoCH markers, returns {type, direction, price, time}
- `detect_session_levels(df)` — previous London/NY session highs/lows

### 2b. `analysis/divergence.py`
- `compute_rsi(df, period=14)` — uses `ta` library RSIIndicator
- `detect_hidden_divergence(df, rsi, lookback=50)` — uses scipy.signal.argrelextrema for swing detection
  - Hidden bullish: price higher low + RSI lower low
  - Hidden bearish: price lower high + RSI higher high
  - Returns point pairs for drawing lines on both price and RSI charts

### 2c. `analysis/bias.py`
- `compute_bias(current_df, daily_df)` — scored 0-100 across 5 factors:
  - Daily trend (price vs 20/50 EMA) — 25 pts
  - Market structure (net BOS direction) — 25 pts
  - Order flow (proximity to unmitigated OB) — 20 pts
  - FVG status (support/resistance from gaps) — 15 pts
  - Session context (kill zone, vs Asia range) — 15 pts
- Returns: {direction: "BULLISH"/"BEARISH"/"NEUTRAL", score, reasoning: [...]}

### 2d. `analysis/levels.py`
- `aggregate_key_levels(current_price, obs, fvgs, liquidity, sessions)` — collects all levels, deduplicates within 0.1%, sorts by distance, returns nearest 6 above + 6 below

### 2e. `analysis/checklist.py`
- `evaluate_checklist(analysis, current_price)` — 7 conditions scored pass/fail:
  1. HTF bias established
  2. Price in premium/discount zone
  3. Liquidity swept
  4. OB present at entry zone
  5. FVG present at entry zone
  6. MSS/CHoCH confirmed on LTF
  7. Hidden divergence present
- Returns: {score, max: 7, items: [{name, status, detail}]}

### 2f. `services/analysis_engine.py`
- `run_full_analysis(df, daily_df)` — orchestrates all modules with per-module error handling (one module failing doesn't break the response)

### 2g. Update `main.py`
- `/api/chart-data/{timeframe}` now returns full payload: candles, rsi, order_blocks, fvg, liquidity_levels, bos_choch, divergences, bias, key_levels, checklist, session, meta

**Checkpoint:** Hit the API endpoint, verify complete JSON with all analysis fields.

---

## Phase 3: Frontend — Chart Overlays + Sidebar
> **PR #3**

**Goal:** Full visual dashboard with all overlays and sidebar cards.

### 3a. Chart overlays (Series Primitives via canvas)
- **Order Blocks**: Semi-transparent rectangles (green 20% for bullish, red 20% for bearish) spanning high-low range. Implement as ISeriesPrimitive with canvas fillRect.
- **FVGs**: Shaded horizontal zones (cyan/magenta 15% opacity), extending right from the FVG candle.
- **Liquidity levels**: Dashed horizontal lines (ctx.setLineDash), gold for EQH, blue for EQL, with touch count labels.
- **BOS/CHoCH markers**: Using series markers — triangles with text labels, colored by direction.
- **Previous session levels**: Dotted horizontal lines (orange for highs, blue for lows), labeled "Prev LDN H" etc.

### 3b. RSI oscillator panel (v5 native multi-pane)
- RSI series added to pane 1: `chart.addSeries(LineSeries, {color: '#7B61FF'}, 1)`
- Overbought (70) / oversold (30) reference lines in same pane
- Scroll, zoom, and crosshair sync is automatic (native v5)
- Hidden divergence lines connecting swing points (green for bullish, red for bearish), with "HD Bull"/"HD Bear" labels

### 3c. Sidebar cards
- **Bias Card**: Large colored direction label, score bar, reasoning bullets
- **Key Levels Card**: Table with nearest levels above/below — type badge, price, distance in pips
- **ICT Checklist Card**: 7 items with checkmark/empty icons, score summary "X/7 conditions met" with color coding

### 3d. Top bar completion
- Session badge (computed in JS from EST time): Asian/London/New York with distinct colors
- Kill zone indicator (active during London 2-5 AM / NY 7-10 AM EST)

**Checkpoint:** Full dashboard functional — chart with all overlays, synced RSI panel, populated sidebar.

---

## Phase 4: Polish + Value-Add Features
> **PR #4**

### 4a. Auto-refresh
- Poll interval based on timeframe: 15m→60s, 1h→2min, 4h→5min, D→10min
- Only refresh when `document.visibilityState === 'visible'`
- Backend cache ensures minimal API consumption

### 4b. Price proximity alerts
- Toast notification + Web Audio API beep when price within 0.2% of a key level
- Track alerted levels to avoid spam

### 4c. Kill zone highlighting
- Vertical semi-transparent bands on chart during kill zone hours
- Only visible on 15m and 1H timeframes

### 4d. Trade idea notes
- Textarea below sidebar, stored in localStorage by date
- No backend needed — purely client-side

### 4e. README.md
- Setup instructions (clone, pip install, .env, uvicorn)
- Feature overview

**Checkpoint:** Complete, polished dashboard ready for daily use.

---

## Caching & Rate Limit Budget

| Timeframe | Cache TTL | Auto-refresh interval |
|-----------|-----------|----------------------|
| 15min     | 2 min     | 60s                  |
| 1h        | 5 min     | 2 min                |
| 4h        | 15 min    | 5 min                |
| 1day      | 60 min    | 10 min               |

**Estimated daily usage:** ~220 calls (well within 800 limit), assuming 8-hour active session with auto-refresh and tab visibility detection.

---

## Error Handling

- **TwelveData failure**: Return stale cached data with `stale: true` flag, frontend shows yellow warning
- **Rate limit hit**: Extend cache TTL automatically, show "Rate limited" indicator
- **Analysis module crash**: Each module wrapped in try/except — partial results returned, errors listed in response
- **Market closed**: Detect weekends (WTI: Sun 6pm–Fri 5pm EST), show "Market Closed" badge, suppress auto-refresh

---

## Verification Plan

1. `pip install -r requirements.txt` succeeds
2. `uvicorn main:app` starts on localhost:8000
3. Candlestick chart renders with correct USOIL data
4. Timeframe switching works (15m/1H/4H/D) — chart updates, overlays recalculate
5. Order blocks, FVGs, liquidity levels visible as colored zones/lines
6. BOS/CHoCH markers appear at correct candles
7. RSI panel syncs scroll/zoom with main chart
8. Hidden divergence lines connect correct swing points
9. Bias card shows direction with reasoning
10. Key levels table populated with nearest levels
11. Checklist shows accurate pass/fail for each condition
12. Auto-refresh updates data without manual intervention
13. Session badge shows correct current session
