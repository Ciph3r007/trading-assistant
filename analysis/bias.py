"""
HTF (Higher Time Frame) bias engine.

Scores the current directional bias 0–100 across five factors using
daily data and returns BULLISH / BEARISH / NEUTRAL with reasoning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
from ta.trend import EMAIndicator

from analysis.ict import detect_bos_choch, detect_order_blocks, detect_fvg

logger = logging.getLogger(__name__)

# Score thresholds
BULL_THRESHOLD = 60
BEAR_THRESHOLD = 40


def compute_bias(current_df: pd.DataFrame, daily_df: pd.DataFrame) -> dict:
    """
    Compute the directional bias using HTF (daily) structure.

    Returns:
        {
            direction: "BULLISH" | "BEARISH" | "NEUTRAL",
            score:     0–100,
            reasoning: [str, ...],
        }
    """
    score    = 50  # neutral start
    reasons: list[str] = []

    score, reasons = _score_ema_trend(daily_df, score, reasons)
    score, reasons = _score_market_structure(daily_df, score, reasons)
    score, reasons = _score_order_flow(current_df, daily_df, score, reasons)
    score, reasons = _score_fvg_context(daily_df, score, reasons)
    score, reasons = _score_session_context(current_df, score, reasons)

    score = max(0, min(100, score))

    direction = (
        "BULLISH" if score >= BULL_THRESHOLD else
        "BEARISH" if score <= BEAR_THRESHOLD else
        "NEUTRAL"
    )

    return {
        "direction": direction,
        "score":     round(score),
        "reasoning": reasons,
    }


# ---------------------------------------------------------------------------
# Factor 1 — EMA trend (25 pts)
# ---------------------------------------------------------------------------

def _score_ema_trend(
    df: pd.DataFrame, score: float, reasons: list[str]
) -> tuple[float, list[str]]:
    """Price vs daily 20-EMA and 50-EMA. Max ±25 pts."""
    if len(df) < 55:
        reasons.append("Insufficient history for EMA trend")
        return score, reasons

    close = df["close"]
    ema20 = EMAIndicator(close=close, window=20).ema_indicator()
    ema50 = EMAIndicator(close=close, window=50).ema_indicator()

    current = float(close.iloc[-1])
    e20     = float(ema20.iloc[-1])
    e50     = float(ema50.iloc[-1])

    above20 = current > e20
    above50 = current > e50
    ema_rising = float(ema20.iloc[-1]) > float(ema20.iloc[-5])

    if above20 and above50:
        score += 25
        reasons.append("Price above daily 20 & 50 EMA — bullish structure")
    elif above20 and not above50:
        score += 10
        reasons.append("Price above 20 EMA but below 50 EMA — mixed")
    elif not above20 and above50:
        score -= 10
        reasons.append("Price below 20 EMA but above 50 EMA — weakening")
    else:
        score -= 25
        reasons.append("Price below daily 20 & 50 EMA — bearish structure")

    if ema_rising:
        reasons.append("Daily 20 EMA trending upward")
    else:
        reasons.append("Daily 20 EMA trending downward")

    return score, reasons


# ---------------------------------------------------------------------------
# Factor 2 — Market structure BOS/CHoCH (25 pts)
# ---------------------------------------------------------------------------

def _score_market_structure(
    df: pd.DataFrame, score: float, reasons: list[str]
) -> tuple[float, list[str]]:
    """Net direction of recent BOS events. Max ±25 pts."""
    try:
        events = detect_bos_choch(df)
    except Exception:
        reasons.append("Market structure analysis unavailable")
        return score, reasons

    if not events:
        reasons.append("No clear BOS/CHoCH on higher timeframe")
        return score, reasons

    # Count last 5 structure events
    recent = events[-5:]
    bull_count = sum(1 for e in recent if e["direction"] == "bullish")
    bear_count = sum(1 for e in recent if e["direction"] == "bearish")

    last = events[-1]
    last_type = f"{last['type']} ({last['direction']})"

    if bull_count > bear_count:
        pts = 15 + (bull_count - bear_count) * 5
        score += min(pts, 25)
        reasons.append(f"Recent HTF structure bullish — last {last_type}")
    elif bear_count > bull_count:
        pts = 15 + (bear_count - bull_count) * 5
        score -= min(pts, 25)
        reasons.append(f"Recent HTF structure bearish — last {last_type}")
    else:
        reasons.append(f"Mixed HTF structure — last event {last_type}")

    return score, reasons


# ---------------------------------------------------------------------------
# Factor 3 — Order flow / OB proximity (20 pts)
# ---------------------------------------------------------------------------

def _score_order_flow(
    current_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    score: float,
    reasons: list[str],
) -> tuple[float, list[str]]:
    """Is price inside or near a daily OB? Max ±20 pts."""
    try:
        obs = detect_order_blocks(daily_df, last_n=3)
    except Exception:
        reasons.append("Order block analysis unavailable")
        return score, reasons

    if not obs:
        reasons.append("No unmitigated HTF order blocks identified")
        return score, reasons

    current = float(current_df["close"].iloc[-1])
    tolerance = current * 0.005  # 0.5% proximity

    in_bull_ob = any(
        ob["bottom"] - tolerance <= current <= ob["top"] + tolerance
        for ob in obs if ob["type"] == "bullish"
    )
    in_bear_ob = any(
        ob["bottom"] - tolerance <= current <= ob["top"] + tolerance
        for ob in obs if ob["type"] == "bearish"
    )

    # Nearest OBs above and below
    bull_below = [ob for ob in obs if ob["type"] == "bullish" and ob["top"] < current]
    bear_above = [ob for ob in obs if ob["type"] == "bearish" and ob["bottom"] > current]

    if in_bull_ob:
        score += 20
        reasons.append("Price trading inside bullish HTF order block (demand zone)")
    elif in_bear_ob:
        score -= 20
        reasons.append("Price trading inside bearish HTF order block (supply zone)")
    elif bull_below:
        nearest = max(bull_below, key=lambda ob: ob["top"])
        dist_pct = (current - nearest["top"]) / current * 100
        score += 8
        reasons.append(f"Bullish HTF OB below at {nearest['top']:.2f} ({dist_pct:.1f}% away)")
    elif bear_above:
        nearest = min(bear_above, key=lambda ob: ob["bottom"])
        dist_pct = (nearest["bottom"] - current) / current * 100
        score -= 8
        reasons.append(f"Bearish HTF OB above at {nearest['bottom']:.2f} ({dist_pct:.1f}% away)")
    else:
        reasons.append("No significant OB proximity")

    return score, reasons


# ---------------------------------------------------------------------------
# Factor 4 — FVG context (15 pts)
# ---------------------------------------------------------------------------

def _score_fvg_context(
    df: pd.DataFrame, score: float, reasons: list[str]
) -> tuple[float, list[str]]:
    """Unmitigated FVGs above/below as support/resistance. Max ±15 pts."""
    try:
        fvgs = detect_fvg(df, max_fvgs=6)
    except Exception:
        reasons.append("FVG analysis unavailable")
        return score, reasons

    if not fvgs:
        reasons.append("No unmitigated HTF FVGs detected")
        return score, reasons

    current = float(df["close"].iloc[-1])
    bull_fvg_below = [f for f in fvgs if f["type"] == "bullish" and f["top"] < current]
    bear_fvg_above = [f for f in fvgs if f["type"] == "bearish" and f["bottom"] > current]

    if bull_fvg_below and not bear_fvg_above:
        score += 15
        reasons.append(f"{len(bull_fvg_below)} unmitigated bullish FVG(s) below as support")
    elif bear_fvg_above and not bull_fvg_below:
        score -= 15
        reasons.append(f"{len(bear_fvg_above)} unmitigated bearish FVG(s) above as resistance")
    elif bull_fvg_below and bear_fvg_above:
        reasons.append("FVGs present both above and below — range-bound context")
    else:
        reasons.append("No nearby FVG support/resistance")

    return score, reasons


# ---------------------------------------------------------------------------
# Factor 5 — Session context (15 pts)
# ---------------------------------------------------------------------------

def _score_session_context(
    df: pd.DataFrame, score: float, reasons: list[str]
) -> tuple[float, list[str]]:
    """Kill zone timing and Asia range position. Max ±15 pts."""
    utc_now = datetime.now(timezone.utc)
    est_now = utc_now - timedelta(hours=5)
    h = est_now.hour

    in_kill_zone = (2 <= h < 5) or (7 <= h < 10)
    session_name = (
        "London kill zone" if 2 <= h < 5 else
        "NY kill zone"     if 7 <= h < 10 else
        "off-session"
    )

    if in_kill_zone:
        score += 10
        reasons.append(f"Currently in {session_name} — elevated probability window")
    else:
        reasons.append(f"Outside kill zones ({session_name})")

    # Asia range position — is price above or below the Asian session?
    try:
        dt = df["datetime"].copy()
        if dt.dt.tz is None:
            dt = dt.dt.tz_localize("UTC")
        est = dt - timedelta(hours=5)
        today = est_now.date()
        asia_mask = (
            (est.dt.date == today) &
            (est.dt.hour >= 19) | (est.dt.hour < 2)
        )
        asia_df = df[asia_mask.values]
        if not asia_df.empty:
            asia_high = float(asia_df["high"].max())
            asia_low  = float(asia_df["low"].min())
            current   = float(df["close"].iloc[-1])
            if current > asia_high:
                score += 5
                reasons.append(f"Price above Asia range (above {asia_high:.2f}) — bullish expansion")
            elif current < asia_low:
                score -= 5
                reasons.append(f"Price below Asia range (below {asia_low:.2f}) — bearish expansion")
            else:
                reasons.append("Price inside Asia range — consolidation")
    except Exception:
        pass

    return score, reasons
