"""
Analysis orchestrator — runs all ICT analysis modules and assembles
the complete payload for the API response.

Each module is wrapped in try/except so a single failure degrades
gracefully rather than breaking the entire response.
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from analysis.ict import (
    detect_order_blocks,
    detect_fvg,
    detect_liquidity,
    detect_bos_choch,
    detect_session_levels,
)
from analysis.divergence import compute_rsi, detect_hidden_divergence
from analysis.bias import compute_bias
from analysis.levels import aggregate_key_levels
from analysis.checklist import evaluate_checklist

logger = logging.getLogger(__name__)


def run_full_analysis(df: pd.DataFrame, daily_df: pd.DataFrame | None = None) -> dict:
    """
    Run all analysis modules on `df` (current timeframe) and `daily_df`
    (for HTF bias). Returns a complete analysis dict ready for JSON
    serialisation.
    """
    if daily_df is None:
        daily_df = df

    current_price = float(df["close"].iloc[-1])
    errors: list[str] = []
    t0 = time.perf_counter()

    # ----------------------------------------------------------------
    # RSI
    # ----------------------------------------------------------------
    rsi_series = None
    rsi_list: list[dict] = []
    try:
        rsi_series = compute_rsi(df)
        rsi_list = [
            {"time": int(row["datetime"].timestamp()), "value": round(float(v), 2)}
            for v, row in zip(rsi_series, df.itertuples())
            if not pd.isna(v)
        ]
    except Exception as exc:
        errors.append(f"rsi: {exc}")
        logger.warning("RSI computation failed: %s", exc)

    # ----------------------------------------------------------------
    # ICT concepts
    # ----------------------------------------------------------------
    order_blocks: list[dict] = []
    try:
        order_blocks = detect_order_blocks(df)
    except Exception as exc:
        errors.append(f"order_blocks: {exc}")
        logger.warning("OB detection failed: %s", exc)

    fvg: list[dict] = []
    try:
        fvg = detect_fvg(df)
    except Exception as exc:
        errors.append(f"fvg: {exc}")
        logger.warning("FVG detection failed: %s", exc)

    liquidity: list[dict] = []
    try:
        liquidity = detect_liquidity(df)
    except Exception as exc:
        errors.append(f"liquidity: {exc}")
        logger.warning("Liquidity detection failed: %s", exc)

    bos_choch: list[dict] = []
    try:
        bos_choch = detect_bos_choch(df)
    except Exception as exc:
        errors.append(f"bos_choch: {exc}")
        logger.warning("BOS/CHoCH detection failed: %s", exc)

    session_levels: dict = {}
    try:
        session_levels = detect_session_levels(df)
    except Exception as exc:
        errors.append(f"session_levels: {exc}")
        logger.warning("Session level detection failed: %s", exc)

    # ----------------------------------------------------------------
    # Hidden divergence
    # ----------------------------------------------------------------
    divergences: list[dict] = []
    if rsi_series is not None:
        try:
            divergences = detect_hidden_divergence(df, rsi_series)
        except Exception as exc:
            errors.append(f"divergences: {exc}")
            logger.warning("Divergence detection failed: %s", exc)

    # ----------------------------------------------------------------
    # HTF bias
    # ----------------------------------------------------------------
    bias = {"direction": "NEUTRAL", "score": 50, "reasoning": []}
    try:
        bias = compute_bias(df, daily_df)
    except Exception as exc:
        errors.append(f"bias: {exc}")
        logger.warning("Bias computation failed: %s", exc)

    # ----------------------------------------------------------------
    # Key levels
    # ----------------------------------------------------------------
    key_levels: list[dict] = []
    try:
        key_levels = aggregate_key_levels(
            current_price,
            order_blocks,
            fvg,
            liquidity,
            session_levels,
        )
    except Exception as exc:
        errors.append(f"key_levels: {exc}")
        logger.warning("Key levels aggregation failed: %s", exc)

    # ----------------------------------------------------------------
    # ICT checklist
    # ----------------------------------------------------------------
    checklist = {"score": 0, "max": 7, "items": []}
    try:
        # Pass daily_df reference through so checklist can compute discount/premium
        analysis_ctx = {
            "bias":             bias,
            "order_blocks":     order_blocks,
            "fvg":              fvg,
            "liquidity_levels": liquidity,
            "bos_choch":        bos_choch,
            "divergences":      divergences,
            "_daily_df":        daily_df,
        }
        checklist = evaluate_checklist(analysis_ctx, current_price)
    except Exception as exc:
        errors.append(f"checklist: {exc}")
        logger.warning("Checklist evaluation failed: %s", exc)

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    if errors:
        logger.warning("Analysis completed with %d error(s): %s", len(errors), errors)
    else:
        logger.debug("Analysis completed in %d ms", elapsed_ms)

    return {
        "rsi":              rsi_list,
        "order_blocks":     order_blocks,
        "fvg":              fvg,
        "liquidity_levels": liquidity,
        "bos_choch":        bos_choch,
        "session_levels":   session_levels,
        "divergences":      divergences,
        "bias":             bias,
        "key_levels":       key_levels,
        "checklist":        checklist,
        "_errors":          errors,
        "_elapsed_ms":      elapsed_ms,
    }
