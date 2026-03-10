# USOIL Trading Dashboard

A locally-run analytics dashboard for manual WTI Crude Oil trading using the ICT framework and hidden divergence entries. Provides directional bias, key level identification, and setup validation — no automated trading.

## Setup

### 1. Clone & enter the project
```bash
git clone <repo-url>
cd trading-assistant
```

### 2. Create the virtual environment (Python 3.12 required)

`smartmoneyconcepts` depends on `numba`, which requires Python ≤ 3.12 and
pre-built binaries. Use [`uv`](https://github.com/astral-sh/uv) to manage
this without touching your system Python:

```bash
# Install uv (once)
pip install uv

# Download Python 3.12 and create the venv
python -m uv python install 3.12
python -m uv venv --python 3.12 .venv
```

### 3. Install dependencies

`numba`/`llvmlite` must be installed from pre-built wheels (no C compiler needed):

```bash
# Windows — install numba wheel first, then everything else
python -m uv pip install --python .venv/Scripts/python.exe --only-binary :all: numba
python -m uv pip install --python .venv/Scripts/python.exe --no-deps smartmoneyconcepts
python -m uv pip install --python .venv/Scripts/python.exe --only-binary :all: pandas scipy
python -m uv pip install --python .venv/Scripts/python.exe fastapi "uvicorn[standard]" twelvedata ta python-dotenv
```

Then activate the venv for subsequent commands:
```bash
.venv\Scripts\activate   # Windows
source .venv/bin/activate  # macOS / Linux
```

### 4. Configure your API key
```bash
cp .env.example .env
# Edit .env and replace "your_key_here" with your TwelveData API key
# Get a free key at https://twelvedata.com
```

### 5. Run the server
```bash
uvicorn main:app --reload
```

Open **http://localhost:8000** in your browser.

---

## Features

### Chart
- **Candlestick chart** — WTI/USD data via TwelveData, timeframes: 15m / 1H / 4H / Daily
- **Order Blocks** — semi-transparent green (bullish) and red (bearish) zones on the chart
- **Fair Value Gaps** — shaded cyan (bullish) and magenta (bearish) horizontal bands
- **Liquidity levels** — dashed lines at equal highs/lows with touch-count labels
- **BOS / CHoCH markers** — triangle markers at break-of-structure and change-of-character candles
- **Session levels** — dotted lines for previous London and New York session highs/lows
- **Kill zone bands** — vertical shaded regions during London (02:00–05:00 EST) and NY (07:00–10:00 EST) kill zones

### RSI Panel
- **RSI(14)** — second chart pane, scroll/zoom synced with the main chart automatically
- **Reference lines** — overbought (70) and oversold (30) levels
- **Hidden divergence lines** — green lines for hidden bullish divergence, red for hidden bearish

### Sidebar
- **Directional bias** — HTF bias engine scoring 5 factors (EMA trend, market structure, OB proximity, FVG context, session). Shows BULLISH / BEARISH / NEUTRAL with score bar and reasoning bullets.
- **Key levels** — table of the nearest levels above and below current price, tagged by type (OB / FVG / Liquidity / Session) with distance in %
- **ICT setup checklist** — 7-condition pass/fail scoring: HTF bias, premium/discount zone, liquidity swept, OB at entry, FVG at entry, MSS/CHoCH confirmed, hidden divergence present
- **Trade notes** — free-text textarea saved in browser localStorage, persists across sessions

### Automation
- **Auto-refresh** — polls for new data at intervals matching the selected timeframe; pauses when the browser tab is hidden to conserve the daily API quota
- **Price proximity alerts** — toast notification and audio beep when price comes within 0.2% of any key level (deduplicated to avoid spam)
- **Session badge** — live indicator in the top bar showing the current trading session (Asian / London / New York)
- **Stale data warning** — yellow indicator when the backend is returning cached data older than its TTL

## API Budget

| Timeframe | Cache TTL | Auto-refresh interval |
|-----------|-----------|----------------------|
| 15 min    | 2 min     | 60 s                 |
| 1 H       | 5 min     | 2 min                |
| 4 H       | 15 min    | 5 min                |
| Daily     | 60 min    | 10 min               |

Estimated daily usage is ~220 calls during an 8-hour active session — well within TwelveData's free-tier limit of 800/day.

## Tech Stack

| Layer    | Technology |
|----------|------------|
| Backend  | Python · FastAPI · uvicorn |
| Frontend | Vanilla JS · TradingView Lightweight Charts v5 |
| Data     | TwelveData API (free tier) |
| Analysis | pandas · numpy · ta · smartmoneyconcepts · scipy |
