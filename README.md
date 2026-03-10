# USOIL Trading Dashboard

A locally-run analytics dashboard for manual WTI Crude Oil trading using the ICT framework and hidden divergence entries. Provides directional bias, key level identification, and setup validation — no automated trading.

## Setup

### 1. Clone & enter the project
```bash
git clone <repo-url>
cd trading-assistant
```

### 2. Create and activate the virtual environment
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
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

- **Candlestick chart** — WTI/USD via TwelveData API, timeframes: 15m / 1H / 4H / Daily
- **ICT overlays** — Order Blocks, Fair Value Gaps, liquidity levels, BOS/CHoCH markers, session levels *(Phase 2+)*
- **RSI panel** — with hidden divergence detection *(Phase 2+)*
- **Directional bias** — HTF bias engine with reasoning *(Phase 2+)*
- **Key levels** — nearest levels above/below price *(Phase 2+)*
- **ICT setup checklist** — 7-condition scoring *(Phase 2+)*
- **Auto-refresh** — respects tab visibility to conserve API quota
- **Trade notes** — saved locally in browser storage

## Tech Stack

| Layer    | Technology |
|----------|------------|
| Backend  | Python · FastAPI · uvicorn |
| Frontend | Vanilla JS · TradingView Lightweight Charts v5 |
| Data     | TwelveData API (free tier) |
| Analysis | pandas · numpy · ta · smartmoneyconcepts |
