"""
TwelveData market data fetching with in-memory caching and rate limiting.

Free tier limits: 8 req/min, 800 calls/day
We cap at 7 req/min and 780/day to leave headroom.
"""

import time
import logging
from datetime import datetime, timezone
from collections import deque
from typing import Optional

import pandas as pd
from twelvedata import TDClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache TTLs (seconds) per interval
# ---------------------------------------------------------------------------
CACHE_TTL: dict[str, int] = {
    "15min": 120,    # 2 min
    "1h":    300,    # 5 min
    "4h":    900,    # 15 min
    "1day":  3600,   # 60 min
}

# Map UI timeframe labels → TwelveData interval strings
TF_MAP: dict[str, str] = {
    "15m":  "15min",
    "1H":   "1h",
    "4H":   "4h",
    "D":    "1day",
    # also accept the canonical forms directly
    "15min": "15min",
    "1h":    "1h",
    "4h":    "4h",
    "1day":  "1day",
}

SYMBOL = "WTI/USD"
MAX_REQ_PER_MIN = 7
MAX_REQ_PER_DAY = 780


class MarketDataService:
    def __init__(self, api_key: str):
        self._client = TDClient(apikey=api_key)
        # Cache: {interval: {"df": pd.DataFrame, "timestamp": float}}
        self._cache: dict[str, dict] = {}
        # Sliding window for per-minute rate limiting
        self._req_timestamps: deque = deque()
        # Daily call counter (resets at midnight UTC)
        self._day_str: str = ""
        self._daily_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        interval: str,
        outputsize: int = 500,
    ) -> pd.DataFrame:
        """
        Return OHLCV DataFrame for WTI/USD at the given interval.
        Serves from cache when fresh; fetches from TwelveData otherwise.
        Falls back to stale cache if rate-limited or on API error.
        """
        td_interval = TF_MAP.get(interval, interval)
        cached = self._cache.get(td_interval)
        ttl = CACHE_TTL.get(td_interval, 120)

        if cached and (time.time() - cached["timestamp"]) < ttl:
            logger.debug("Cache hit for %s", td_interval)
            return cached["df"].copy()

        # Try to fetch fresh data
        if self._is_rate_limited():
            if cached:
                logger.warning("Rate limited — returning stale cache for %s", td_interval)
                return cached["df"].copy()
            raise RuntimeError("Rate limited and no cached data available")

        try:
            df = self._fetch(td_interval, outputsize)
            self._cache[td_interval] = {"df": df, "timestamp": time.time()}
            return df.copy()
        except Exception as exc:
            logger.error("TwelveData fetch failed for %s: %s", td_interval, exc)
            if cached:
                logger.warning("Returning stale cache for %s", td_interval)
                return cached["df"].copy()
            raise

    def get_multi_tf_data(
        self, requested_interval: str, outputsize: int = 500
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch the requested timeframe plus the daily TF (needed by bias engine).
        Returns a dict: {"current": df, "daily": df}
        """
        td_interval = TF_MAP.get(requested_interval, requested_interval)
        result: dict[str, pd.DataFrame] = {}

        result["current"] = self.get_ohlcv(td_interval, outputsize)

        if td_interval != "1day":
            result["daily"] = self.get_ohlcv("1day", outputsize=300)
        else:
            result["daily"] = result["current"]

        return result

    def get_cache_stats(self) -> dict:
        now = time.time()
        stats = {}
        for interval, entry in self._cache.items():
            age = now - entry["timestamp"]
            ttl = CACHE_TTL.get(interval, 120)
            stats[interval] = {
                "age_seconds": round(age),
                "ttl_seconds": ttl,
                "fresh": age < ttl,
                "rows": len(entry["df"]),
            }
        return {
            "cached_intervals": stats,
            "daily_calls": self._daily_count,
            "daily_limit": MAX_REQ_PER_DAY,
            "calls_remaining": MAX_REQ_PER_DAY - self._daily_count,
        }

    def is_stale(self, interval: str) -> bool:
        td_interval = TF_MAP.get(interval, interval)
        cached = self._cache.get(td_interval)
        if not cached:
            return True
        ttl = CACHE_TTL.get(td_interval, 120)
        return (time.time() - cached["timestamp"]) >= ttl

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(self, td_interval: str, outputsize: int) -> pd.DataFrame:
        self._record_request()
        logger.info("Fetching %s %s (%d bars)", SYMBOL, td_interval, outputsize)

        ts = (
            self._client
            .time_series(
                symbol=SYMBOL,
                interval=td_interval,
                outputsize=outputsize,
                order="ASC",
            )
            .as_pandas()
        )

        # Normalise columns
        ts.index.name = "datetime"
        ts = ts.reset_index()
        ts.columns = [c.lower() for c in ts.columns]
        ts["datetime"] = pd.to_datetime(ts["datetime"])
        ts = ts.sort_values("datetime").reset_index(drop=True)

        for col in ["open", "high", "low", "close"]:
            ts[col] = pd.to_numeric(ts[col], errors="coerce")
        if "volume" in ts.columns:
            ts["volume"] = pd.to_numeric(ts["volume"], errors="coerce").fillna(0)
        else:
            ts["volume"] = 0.0

        ts = ts.dropna(subset=["open", "high", "low", "close"])
        return ts

    def _is_rate_limited(self) -> bool:
        now = time.time()
        # Trim timestamps older than 60 seconds
        while self._req_timestamps and now - self._req_timestamps[0] > 60:
            self._req_timestamps.popleft()

        if len(self._req_timestamps) >= MAX_REQ_PER_MIN:
            return True

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day_str:
            self._day_str = today
            self._daily_count = 0

        if self._daily_count >= MAX_REQ_PER_DAY:
            return True

        return False

    def _record_request(self):
        now = time.time()
        self._req_timestamps.append(now)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day_str:
            self._day_str = today
            self._daily_count = 0
        self._daily_count += 1


# Module-level singleton — initialised in main.py startup
_service: Optional[MarketDataService] = None


def init_service(api_key: str) -> None:
    global _service
    _service = MarketDataService(api_key)


def get_service() -> MarketDataService:
    if _service is None:
        raise RuntimeError("MarketDataService not initialised — call init_service() first")
    return _service
