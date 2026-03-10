"""
Key levels aggregation.

Collects all significant price levels from OBs, FVGs, liquidity clusters,
and session highs/lows. Deduplicates within 0.1% tolerance and returns
the nearest 6 above and 6 below current price.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

DEDUP_TOLERANCE = 0.001  # 0.1%


def aggregate_key_levels(
    current_price: float,
    order_blocks: list[dict],
    fvgs: list[dict],
    liquidity: list[dict],
    session_levels: dict,
    max_each_side: int = 6,
) -> list[dict]:
    """
    Aggregate all levels, deduplicate within 0.1%, and return nearest
    `max_each_side` above and below current price.

    Each returned level:
    {
        price:      float,
        type:       "OB" | "FVG" | "Liquidity" | "Session",
        direction:  "support" | "resistance",
        distance_pct: float,
    }
    """
    raw: list[dict] = []

    # Order blocks — use midpoint as the level, label by type
    for ob in order_blocks:
        mid = (ob["top"] + ob["bottom"]) / 2
        raw.append({
            "price":     round(mid, 3),
            "type":      "OB",
            "direction": "support" if ob["type"] == "bullish" else "resistance",
            "_ob_top":   ob["top"],
            "_ob_bot":   ob["bottom"],
        })

    # FVGs — use midpoint
    for fvg in fvgs:
        mid = (fvg["top"] + fvg["bottom"]) / 2
        raw.append({
            "price":     round(mid, 3),
            "type":      "FVG",
            "direction": "support" if fvg["type"] == "bullish" else "resistance",
        })

    # Liquidity clusters
    for liq in liquidity:
        raw.append({
            "price":     liq["level"],
            "type":      "Liquidity",
            "direction": "resistance" if liq["type"] == "high" else "support",
            "_label":    liq.get("label", ""),
        })

    # Session levels
    session_map = {
        "prev_london_high": ("Session", "resistance"),
        "prev_london_low":  ("Session", "support"),
        "prev_ny_high":     ("Session", "resistance"),
        "prev_ny_low":      ("Session", "support"),
    }
    for key, (ltype, direction) in session_map.items():
        val = session_levels.get(key)
        if val is not None:
            raw.append({
                "price":     round(float(val), 3),
                "type":      ltype,
                "direction": direction,
                "_label":    key.replace("_", " ").title(),
            })

    if not raw:
        return []

    # Deduplicate within tolerance — merge nearby levels into one
    raw.sort(key=lambda x: x["price"])
    merged = _deduplicate(raw, current_price, DEDUP_TOLERANCE)

    # Add distance and split above/below
    for level in merged:
        level["distance_pct"] = round(
            abs(level["price"] - current_price) / current_price * 100, 3
        )

    above = sorted(
        [l for l in merged if l["price"] > current_price],
        key=lambda x: x["price"]
    )[:max_each_side]

    below = sorted(
        [l for l in merged if l["price"] < current_price],
        key=lambda x: -x["price"]
    )[:max_each_side]

    # Clean up private fields
    for l in above + below:
        for k in ["_ob_top", "_ob_bot", "_label"]:
            l.pop(k, None)

    return above + below


def _deduplicate(levels: list[dict], current_price: float, tol: float) -> list[dict]:
    """
    Merge levels that are within `tol` of each other.
    Prefer: Liquidity > OB > FVG > Session (for label priority).
    """
    TYPE_PRIORITY = {"Liquidity": 0, "OB": 1, "FVG": 2, "Session": 3}

    merged: list[dict] = []
    used = [False] * len(levels)

    for i, lv in enumerate(levels):
        if used[i]:
            continue
        group = [lv]
        for j in range(i + 1, len(levels)):
            if used[j]:
                continue
            if abs(levels[j]["price"] - lv["price"]) / max(lv["price"], 1e-9) <= tol:
                group.append(levels[j])
                used[j] = True

        # Pick representative: average price, highest-priority type
        avg_price = float(np.mean([g["price"] for g in group]))
        best = min(group, key=lambda g: TYPE_PRIORITY.get(g["type"], 99))
        merged.append({
            "price":     round(avg_price, 3),
            "type":      best["type"],
            "direction": best["direction"],
        })

    return merged
