"""
ICT concept detection: Order Blocks, Fair Value Gaps, Liquidity levels,
BOS/CHoCH markers, and previous session highs/lows.

Uses the smartmoneyconcepts package where available, with pure
pandas/numpy fallbacks for robustness.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _swing_highs_lows(df: pd.DataFrame, swing_length: int = 5) -> pd.DataFrame:
    """
    Identify swing highs and lows using a rolling window.
    Returns a DataFrame with columns: HighLow (1=high, -1=low), Level.
    """
    highs = df["high"].rolling(window=2 * swing_length + 1, center=True).max()
    lows  = df["low"].rolling(window=2 * swing_length + 1, center=True).min()

    result = pd.DataFrame(index=df.index, columns=["HighLow", "Level"], dtype=float)
    result["HighLow"] = np.nan
    result["Level"]   = np.nan

    swing_high_mask = df["high"] == highs
    swing_low_mask  = df["low"]  == lows

    result.loc[swing_high_mask, "HighLow"] = 1.0
    result.loc[swing_high_mask, "Level"]   = df.loc[swing_high_mask, "high"]
    result.loc[swing_low_mask,  "HighLow"] = -1.0
    result.loc[swing_low_mask,  "Level"]   = df.loc[swing_low_mask, "low"]

    # Prefer high over low when both at same bar
    both = swing_high_mask & swing_low_mask
    result.loc[both, "HighLow"] = 1.0
    result.loc[both, "Level"]   = df.loc[both, "high"]

    return result


# ---------------------------------------------------------------------------
# Order Blocks
# ---------------------------------------------------------------------------

def detect_order_blocks(df: pd.DataFrame, last_n: int = 5) -> list[dict]:
    """
    Identify the last N bullish and N bearish unmitigated order blocks.

    Bullish OB  — last bearish (down-close) candle before a strong bullish impulse
    Bearish OB  — last bullish (up-close) candle before a strong bearish impulse

    Returns list of {type, top, bottom, start_time, end_time, mitigated}.
    """
    try:
        from smartmoneyconcepts import smc
        swing = _swing_highs_lows(df)
        swing_col = swing.rename(columns={"HighLow": "HighLow", "Level": "Level"})

        ob = smc.ob(df, swing_col)
        results = []
        current_close = float(df["close"].iloc[-1])
        last_time = int(df["datetime"].iloc[-1].timestamp())

        for i, row in ob.iterrows():
            if pd.isna(row.get("OB")) or row["OB"] == 0:
                continue
            ob_type = "bullish" if row["OB"] == 1 else "bearish"
            top    = float(row["Top"])
            bottom = float(row["Bottom"])

            # Mitigated: price has traded through the OB
            mitigated = (ob_type == "bullish" and current_close < bottom) or \
                        (ob_type == "bearish" and current_close > top)

            results.append({
                "type":       ob_type,
                "top":        round(top, 3),
                "bottom":     round(bottom, 3),
                "start_time": int(df["datetime"].iloc[i].timestamp()),
                "end_time":   last_time,
                "mitigated":  mitigated,
            })

        # Filter unmitigated, keep last N of each type
        bull = [r for r in results if r["type"] == "bullish" and not r["mitigated"]][-last_n:]
        bear = [r for r in results if r["type"] == "bearish" and not r["mitigated"]][-last_n:]
        return bull + bear

    except Exception as exc:
        logger.warning("OB detection via smc failed (%s), using fallback", exc)
        return _detect_ob_fallback(df, last_n)


def _detect_ob_fallback(df: pd.DataFrame, last_n: int = 5) -> list[dict]:
    """Pure pandas OB detection fallback."""
    results = []
    closes = df["close"].values
    opens  = df["open"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["datetime"].values
    n      = len(df)

    # Minimum impulse: 3× average true range
    atr = np.mean(highs - lows)
    min_impulse = 3 * atr

    last_time = int(pd.Timestamp(times[-1]).timestamp())
    current_close = float(closes[-1])

    for i in range(2, n - 1):
        # Bullish OB: bearish candle (close < open) followed by bullish impulse
        if closes[i] < opens[i]:
            # Check for bullish impulse in next few candles
            impulse = max(closes[i+1:min(i+4, n)]) - closes[i]
            if impulse >= min_impulse:
                top    = float(highs[i])
                bottom = float(lows[i])
                mitigated = current_close < bottom
                results.append({
                    "type":       "bullish",
                    "top":        round(top, 3),
                    "bottom":     round(bottom, 3),
                    "start_time": int(pd.Timestamp(times[i]).timestamp()),
                    "end_time":   last_time,
                    "mitigated":  mitigated,
                })

        # Bearish OB: bullish candle (close > open) followed by bearish impulse
        elif closes[i] > opens[i]:
            impulse = closes[i] - min(closes[i+1:min(i+4, n)])
            if impulse >= min_impulse:
                top    = float(highs[i])
                bottom = float(lows[i])
                mitigated = current_close > top
                results.append({
                    "type":       "bearish",
                    "top":        round(top, 3),
                    "bottom":     round(bottom, 3),
                    "start_time": int(pd.Timestamp(times[i]).timestamp()),
                    "end_time":   last_time,
                    "mitigated":  mitigated,
                })

    bull = [r for r in results if r["type"] == "bullish" and not r["mitigated"]][-last_n:]
    bear = [r for r in results if r["type"] == "bearish" and not r["mitigated"]][-last_n:]
    return bull + bear


# ---------------------------------------------------------------------------
# Fair Value Gaps
# ---------------------------------------------------------------------------

def detect_fvg(df: pd.DataFrame, max_fvgs: int = 10) -> list[dict]:
    """
    Detect unmitigated Fair Value Gaps (3-candle pattern).

    Bullish FVG:  candle[i-1].high < candle[i+1].low  (gap up)
    Bearish FVG:  candle[i-1].low  > candle[i+1].high (gap down)

    Returns list of {type, top, bottom, start_time, mitigated}.
    """
    try:
        from smartmoneyconcepts import smc
        fvg = smc.fvg(df)
        results = []
        current_close = float(df["close"].iloc[-1])

        for i, row in fvg.iterrows():
            if pd.isna(row.get("FVG")) or row["FVG"] == 0:
                continue
            fvg_type = "bullish" if row["FVG"] == 1 else "bearish"
            top    = float(row["Top"])
            bottom = float(row["Bottom"])

            mitigated = (fvg_type == "bullish" and current_close < bottom) or \
                        (fvg_type == "bearish" and current_close > top)
            if not mitigated:
                results.append({
                    "type":       fvg_type,
                    "top":        round(top, 3),
                    "bottom":     round(bottom, 3),
                    "start_time": int(df["datetime"].iloc[i].timestamp()),
                    "mitigated":  False,
                })

        return results[-max_fvgs:]

    except Exception as exc:
        logger.warning("FVG detection via smc failed (%s), using fallback", exc)
        return _detect_fvg_fallback(df, max_fvgs)


def _detect_fvg_fallback(df: pd.DataFrame, max_fvgs: int = 10) -> list[dict]:
    """Pure pandas FVG fallback."""
    results = []
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    times  = df["datetime"].values
    current_close = float(closes[-1])

    for i in range(1, len(df) - 1):
        # Bullish FVG
        if highs[i - 1] < lows[i + 1]:
            top    = float(lows[i + 1])
            bottom = float(highs[i - 1])
            mitigated = current_close < bottom
            if not mitigated:
                results.append({
                    "type":       "bullish",
                    "top":        round(top, 3),
                    "bottom":     round(bottom, 3),
                    "start_time": int(pd.Timestamp(times[i]).timestamp()),
                    "mitigated":  False,
                })
        # Bearish FVG
        elif lows[i - 1] > highs[i + 1]:
            top    = float(lows[i - 1])
            bottom = float(highs[i + 1])
            mitigated = current_close > top
            if not mitigated:
                results.append({
                    "type":       "bearish",
                    "top":        round(top, 3),
                    "bottom":     round(bottom, 3),
                    "start_time": int(pd.Timestamp(times[i]).timestamp()),
                    "mitigated":  False,
                })

    return results[-max_fvgs:]


# ---------------------------------------------------------------------------
# Liquidity levels (Equal Highs / Equal Lows)
# ---------------------------------------------------------------------------

def detect_liquidity(df: pd.DataFrame, tolerance: float = 0.001, lookback: int = 50) -> list[dict]:
    """
    Identify equal highs (EQH) and equal lows (EQL) — clusters of swing
    points within `tolerance` (0.1%) of each other.

    Returns list of {level, type ("high"|"low"), touches, label}.
    """
    try:
        from smartmoneyconcepts import smc
        liq = smc.liquidity(df, range_percent=tolerance * 100)
        results = []

        for i, row in liq.iterrows():
            if pd.isna(row.get("Liquidity")) or row["Liquidity"] == 0:
                continue
            liq_type = "high" if row["Liquidity"] == 1 else "low"
            results.append({
                "level":  round(float(row["Level"]), 3),
                "type":   liq_type,
                "label":  "EQH" if liq_type == "high" else "EQL",
                "touches": int(row.get("Swept", 1)),
                "time":   int(df["datetime"].iloc[i].timestamp()),
            })

        return results

    except Exception as exc:
        logger.warning("Liquidity detection via smc failed (%s), using fallback", exc)
        return _detect_liquidity_fallback(df, tolerance, lookback)


def _detect_liquidity_fallback(
    df: pd.DataFrame, tolerance: float = 0.001, lookback: int = 50
) -> list[dict]:
    """Pure pandas equal highs/lows fallback."""
    sub   = df.tail(lookback).copy()
    swing = _swing_highs_lows(sub)
    highs = swing[swing["HighLow"] == 1.0]["Level"].dropna()
    lows  = swing[swing["HighLow"] == -1.0]["Level"].dropna()

    results = []

    def cluster(series: pd.Series, liq_type: str):
        if series.empty:
            return
        vals = series.sort_values().values
        visited = set()
        for i, v in enumerate(vals):
            if i in visited:
                continue
            group = [v]
            for j in range(i + 1, len(vals)):
                if abs(vals[j] - v) / v <= tolerance:
                    group.append(vals[j])
                    visited.add(j)
            if len(group) >= 2:
                level = float(np.mean(group))
                label = "EQH" if liq_type == "high" else "EQL"
                results.append({
                    "level":   round(level, 3),
                    "type":    liq_type,
                    "label":   label,
                    "touches": len(group),
                    "time":    int(sub["datetime"].iloc[-1].timestamp()),
                })

    cluster(highs, "high")
    cluster(lows,  "low")
    return results


# ---------------------------------------------------------------------------
# BOS / CHoCH
# ---------------------------------------------------------------------------

def detect_bos_choch(df: pd.DataFrame) -> list[dict]:
    """
    Detect Break of Structure (BOS) and Change of Character (CHoCH).

    BOS:   price breaks a swing high/low in the direction of the current trend
    CHoCH: price breaks a swing high/low against the current trend (structure shift)

    Returns list of {type ("BOS"|"CHoCH"), direction ("bullish"|"bearish"), price, time}.
    """
    try:
        from smartmoneyconcepts import smc
        swing = _swing_highs_lows(df)

        bos   = smc.bos(df, swing, close_break=True)
        choch = smc.choch(df, swing, close_break=True)

        results = []
        for i, row in bos.iterrows():
            if pd.isna(row.get("BOS")) or row["BOS"] == 0:
                continue
            results.append({
                "type":      "BOS",
                "direction": "bullish" if row["BOS"] == 1 else "bearish",
                "price":     round(float(row["Level"]), 3),
                "time":      int(df["datetime"].iloc[i].timestamp()),
            })

        for i, row in choch.iterrows():
            if pd.isna(row.get("CHOCH")) or row["CHOCH"] == 0:
                continue
            results.append({
                "type":      "CHoCH",
                "direction": "bullish" if row["CHOCH"] == 1 else "bearish",
                "price":     round(float(row["Level"]), 3),
                "time":      int(df["datetime"].iloc[i].timestamp()),
            })

        # Return most recent 20 events sorted by time
        return sorted(results, key=lambda r: r["time"])[-20:]

    except Exception as exc:
        logger.warning("BOS/CHoCH detection via smc failed (%s), using fallback", exc)
        return _detect_bos_choch_fallback(df)


def _detect_bos_choch_fallback(df: pd.DataFrame) -> list[dict]:
    """Pure pandas BOS/CHoCH fallback using swing structure."""
    swing  = _swing_highs_lows(df)
    closes = df["close"].values
    times  = df["datetime"].values
    results = []

    prev_high: float | None = None
    prev_low:  float | None = None
    trend: str | None = None  # "up" or "down"

    for i in range(len(df)):
        hl = swing["HighLow"].iloc[i]
        lv = swing["Level"].iloc[i]
        c  = float(closes[i])
        t  = int(pd.Timestamp(times[i]).timestamp())

        if pd.isna(hl):
            # Check for structure breaks
            if prev_high is not None and c > prev_high:
                ev_type = "BOS" if trend == "up" else "CHoCH"
                results.append({"type": ev_type, "direction": "bullish",
                                 "price": round(prev_high, 3), "time": t})
                prev_high = c
                trend = "up"
            elif prev_low is not None and c < prev_low:
                ev_type = "BOS" if trend == "down" else "CHoCH"
                results.append({"type": ev_type, "direction": "bearish",
                                 "price": round(prev_low, 3), "time": t})
                prev_low = c
                trend = "down"
            continue

        if hl == 1.0:
            prev_high = float(lv)
        elif hl == -1.0:
            prev_low = float(lv)

    return results[-20:]


# ---------------------------------------------------------------------------
# Previous session levels
# ---------------------------------------------------------------------------

def detect_session_levels(df: pd.DataFrame) -> dict:
    """
    Return previous New York and London session highs/lows.

    Session times (EST = UTC-5):
      London:   02:00 – 10:00
      New York: 08:00 – 17:00
    """
    if df.empty:
        return {}

    # Ensure UTC-aware datetime
    dt = df["datetime"].copy()
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize("UTC")
    else:
        dt = dt.dt.tz_convert("UTC")

    est_offset = timedelta(hours=-5)
    dt_est = dt + est_offset
    df = df.copy()
    df["_h"] = dt_est.dt.hour

    today_est = (datetime.now(timezone.utc) + est_offset).date()

    def session_hl(h_start: int, h_end: int, target_date: date) -> tuple[float | None, float | None]:
        mask = (
            (dt_est.dt.date == target_date) &
            (df["_h"] >= h_start) &
            (df["_h"] < h_end)
        )
        sub = df[mask.values]
        if sub.empty:
            return None, None
        return float(sub["high"].max()), float(sub["low"].min())

    prev_date = today_est - timedelta(days=1)
    # Skip weekends
    while prev_date.weekday() >= 5:
        prev_date -= timedelta(days=1)

    ldn_h, ldn_l = session_hl(2, 10, prev_date)
    ny_h,  ny_l  = session_hl(8, 17, prev_date)

    result = {}
    if ldn_h:
        result["prev_london_high"] = round(ldn_h, 3)
        result["prev_london_low"]  = round(ldn_l, 3)
    if ny_h:
        result["prev_ny_high"] = round(ny_h, 3)
        result["prev_ny_low"]  = round(ny_l, 3)

    return result
