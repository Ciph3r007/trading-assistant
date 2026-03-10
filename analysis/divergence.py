"""
RSI computation and hidden divergence detection.

Hidden divergence is a trend-continuation signal:
  Bullish hidden divergence: price makes a higher low, RSI makes a lower low
  Bearish hidden divergence: price makes a lower high, RSI makes a higher high
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from ta.momentum import RSIIndicator

logger = logging.getLogger(__name__)

# Minimum distance between swing points (candles)
MIN_SWING_DIST = 5
# RSI thresholds — only look for bull div when RSI is below mid, bear above mid
RSI_BULL_ZONE = 55
RSI_BEAR_ZONE = 45


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Return RSI(period) series aligned to df's index."""
    rsi = RSIIndicator(close=df["close"], window=period).rsi()
    rsi.index = df.index
    return rsi


def detect_hidden_divergence(
    df: pd.DataFrame,
    rsi: pd.Series,
    lookback: int = 100,
) -> list[dict]:
    """
    Detect hidden divergence between price and RSI over the last `lookback` bars.

    Returns list of:
    {
        type:         "hidden_bullish" | "hidden_bearish",
        price_points: [{time, value}, {time, value}],   # two swing points on price
        rsi_points:   [{time, value}, {time, value}],   # corresponding RSI points
        label:        "HD Bull" | "HD Bear",
    }
    """
    if len(df) < lookback + MIN_SWING_DIST * 2:
        return []

    sub_df  = df.tail(lookback).copy().reset_index(drop=True)
    sub_rsi = rsi.tail(lookback).copy().reset_index(drop=True)

    closes = sub_df["close"].values.astype(float)
    lows   = sub_df["low"].values.astype(float)
    highs  = sub_df["high"].values.astype(float)
    rsi_v  = sub_rsi.values.astype(float)

    # Mask NaN RSI (first ~14 bars)
    valid = ~np.isnan(rsi_v)
    if valid.sum() < MIN_SWING_DIST * 4:
        return []

    results = []

    # ------------------------------------------------------------------
    # Bullish hidden divergence:
    #   Price: higher low  (low[j] > low[i],  j > i)
    #   RSI:   lower low   (rsi[j] < rsi[i])
    # ------------------------------------------------------------------
    price_troughs = argrelextrema(lows, np.less, order=MIN_SWING_DIST)[0]
    rsi_troughs   = argrelextrema(rsi_v, np.less, order=MIN_SWING_DIST)[0]

    price_troughs = price_troughs[valid[price_troughs]]
    rsi_troughs   = rsi_troughs[valid[rsi_troughs]]

    for i in range(len(price_troughs) - 1):
        p1_idx = price_troughs[i]
        for j in range(i + 1, len(price_troughs)):
            p2_idx = price_troughs[j]
            if p2_idx - p1_idx < MIN_SWING_DIST:
                continue

            # Price: higher low
            if lows[p2_idx] <= lows[p1_idx]:
                continue

            # Find closest RSI troughs to these price points
            r1_idx = _nearest(rsi_troughs, p1_idx)
            r2_idx = _nearest(rsi_troughs, p2_idx)
            if r1_idx is None or r2_idx is None or r1_idx == r2_idx:
                continue

            # RSI: lower low
            if rsi_v[r2_idx] >= rsi_v[r1_idx]:
                continue

            # Only flag if RSI is below midline (genuine oversold region)
            if rsi_v[r2_idx] > RSI_BULL_ZONE:
                continue

            results.append(_build_divergence(
                "hidden_bullish", "HD Bull",
                sub_df, rsi_v,
                p1_idx, p2_idx,
                r1_idx, r2_idx,
                use_lows=True,
            ))

    # ------------------------------------------------------------------
    # Bearish hidden divergence:
    #   Price: lower high  (high[j] < high[i], j > i)
    #   RSI:   higher high (rsi[j] > rsi[i])
    # ------------------------------------------------------------------
    price_peaks = argrelextrema(highs, np.greater, order=MIN_SWING_DIST)[0]
    rsi_peaks   = argrelextrema(rsi_v, np.greater, order=MIN_SWING_DIST)[0]

    price_peaks = price_peaks[valid[price_peaks]]
    rsi_peaks   = rsi_peaks[valid[rsi_peaks]]

    for i in range(len(price_peaks) - 1):
        p1_idx = price_peaks[i]
        for j in range(i + 1, len(price_peaks)):
            p2_idx = price_peaks[j]
            if p2_idx - p1_idx < MIN_SWING_DIST:
                continue

            # Price: lower high
            if highs[p2_idx] >= highs[p1_idx]:
                continue

            r1_idx = _nearest(rsi_peaks, p1_idx)
            r2_idx = _nearest(rsi_peaks, p2_idx)
            if r1_idx is None or r2_idx is None or r1_idx == r2_idx:
                continue

            # RSI: higher high
            if rsi_v[r2_idx] <= rsi_v[r1_idx]:
                continue

            if rsi_v[r2_idx] < RSI_BEAR_ZONE:
                continue

            results.append(_build_divergence(
                "hidden_bearish", "HD Bear",
                sub_df, rsi_v,
                p1_idx, p2_idx,
                r1_idx, r2_idx,
                use_lows=False,
            ))

    # Deduplicate — keep most recent non-overlapping divergences
    return _deduplicate(results)[-5:]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _nearest(indices: np.ndarray, target: int, window: int = 8) -> int | None:
    """Return the index in `indices` closest to `target`, within `window` bars."""
    if len(indices) == 0:
        return None
    diffs = np.abs(indices - target)
    closest = int(np.argmin(diffs))
    if diffs[closest] > window:
        return None
    return int(indices[closest])


def _build_divergence(
    div_type: str,
    label: str,
    sub_df: pd.DataFrame,
    rsi_v: np.ndarray,
    p1: int, p2: int,
    r1: int, r2: int,
    use_lows: bool,
) -> dict:
    price_col = "low" if use_lows else "high"
    return {
        "type":  div_type,
        "label": label,
        "price_points": [
            {
                "time":  int(sub_df["datetime"].iloc[p1].timestamp()),
                "value": round(float(sub_df[price_col].iloc[p1]), 3),
            },
            {
                "time":  int(sub_df["datetime"].iloc[p2].timestamp()),
                "value": round(float(sub_df[price_col].iloc[p2]), 3),
            },
        ],
        "rsi_points": [
            {
                "time":  int(sub_df["datetime"].iloc[r1].timestamp()),
                "value": round(float(rsi_v[r1]), 2),
            },
            {
                "time":  int(sub_df["datetime"].iloc[r2].timestamp()),
                "value": round(float(rsi_v[r2]), 2),
            },
        ],
    }


def _deduplicate(results: list[dict]) -> list[dict]:
    """Remove divergences that share a swing point with a later one."""
    seen_times: set[int] = set()
    out = []
    for r in reversed(results):
        times = {pt["time"] for pt in r["price_points"]}
        if times & seen_times:
            continue
        seen_times |= times
        out.append(r)
    return list(reversed(out))
