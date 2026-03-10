"""
USOIL Trading Analytics Dashboard — FastAPI backend.

Run with:
    uvicorn main:app --reload

Opens at http://localhost:8000
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from services.market_data import TF_MAP, get_service, init_service

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="USOIL Trading Dashboard", version="0.1.0")

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    api_key = os.getenv("TWELVEDATA_API_KEY", "")
    if not api_key or api_key == "your_key_here":
        logger.warning(
            "TWELVEDATA_API_KEY is not set. "
            "Copy .env.example to .env and add your key."
        )
    init_service(api_key)
    logger.info("MarketDataService initialised")


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    svc = get_service()
    return {"status": "ok", "cache": svc.get_cache_stats()}


# ---------------------------------------------------------------------------
# Chart data
# ---------------------------------------------------------------------------

VALID_TIMEFRAMES = set(TF_MAP.keys())


@app.get("/api/chart-data/{timeframe}")
async def chart_data(timeframe: str):
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timeframe '{timeframe}'. Valid options: {sorted(VALID_TIMEFRAMES)}",
        )

    svc = get_service()
    stale = False

    try:
        tf_data = svc.get_multi_tf_data(timeframe)
        df = tf_data["current"]
        stale = svc.is_stale(timeframe)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to fetch chart data for %s", timeframe)
        raise HTTPException(status_code=500, detail=str(exc))

    from services.market_data import TF_MAP
    td_interval = TF_MAP.get(timeframe, timeframe)

    # Build candle list for TradingView Lightweight Charts
    # LW Charts expects {time: unix_timestamp, open, high, low, close}
    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time": int(row["datetime"].timestamp()),
            "open":  round(float(row["open"]),  3),
            "high":  round(float(row["high"]),  3),
            "low":   round(float(row["low"]),   3),
            "close": round(float(row["close"]), 3),
        })

    current_price = float(df["close"].iloc[-1]) if len(df) else None

    return JSONResponse({
        "candles": candles,
        "current_price": current_price,
        "timeframe": td_interval,
        "symbol": "WTI/USD",
        # Phase 2 will populate these:
        "rsi":              [],
        "order_blocks":     [],
        "fvg":              [],
        "liquidity_levels": [],
        "bos_choch":        [],
        "divergences":      [],
        "bias":             {"direction": "NEUTRAL", "score": 50, "reasoning": []},
        "key_levels":       [],
        "checklist":        {"score": 0, "max": 7, "items": []},
        "session":          _current_session(),
        "kill_zones":       _kill_zones(),
        "meta": {
            "stale":     stale,
            "cached":    not stale,
            "bar_count": len(candles),
            "cache_stats": svc.get_cache_stats(),
        },
    })


# ---------------------------------------------------------------------------
# Session helpers (pure Python, no external deps)
# ---------------------------------------------------------------------------

def _current_session() -> str:
    """Return the current trading session name based on UTC time."""
    from datetime import datetime, timezone, timedelta
    utc_now = datetime.now(timezone.utc)
    # EST = UTC-5 (ignoring DST for simplicity; WTI follows clock closely)
    est_now = utc_now - timedelta(hours=5)
    h = est_now.hour

    # Asia:     7 PM – 2 AM  EST  (19-24 + 0-2)
    # London:   2 AM – 10 AM EST
    # New York: 8 AM – 5 PM  EST
    if 2 <= h < 8:
        return "London"
    elif 8 <= h < 17:
        return "New York"
    elif h >= 19 or h < 2:
        return "Asian"
    else:
        return "Off"


def _kill_zones() -> list[dict]:
    """
    Return kill zone time windows for today (UTC unix timestamps).
    London kill zone:   02:00–05:00 EST
    New York kill zone: 07:00–10:00 EST
    """
    from datetime import datetime, timezone, timedelta
    utc_now = datetime.now(timezone.utc)
    est_today = (utc_now - timedelta(hours=5)).date()

    def est_to_utc_ts(h: int, m: int = 0) -> int:
        dt = datetime(est_today.year, est_today.month, est_today.day, h, m,
                      tzinfo=timezone(timedelta(hours=-5)))
        return int(dt.timestamp())

    return [
        {
            "name":  "London Kill Zone",
            "start": est_to_utc_ts(2),
            "end":   est_to_utc_ts(5),
            "color": "rgba(30,144,255,0.08)",
        },
        {
            "name":  "NY Kill Zone",
            "start": est_to_utc_ts(7),
            "end":   est_to_utc_ts(10),
            "color": "rgba(255,165,0,0.08)",
        },
    ]
