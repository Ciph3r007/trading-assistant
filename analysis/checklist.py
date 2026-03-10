"""
ICT setup checklist — 7 conditions for a valid ICT entry.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def evaluate_checklist(analysis: dict, current_price: float) -> dict:
    """
    Evaluate 7 ICT setup conditions and return a scored checklist.

    Returns:
    {
        score: int,
        max:   7,
        items: [{name, status: "pass"|"fail"|"partial", detail}, ...],
    }
    """
    items = [
        _check_bias(analysis),
        _check_premium_discount(analysis, current_price),
        _check_liquidity_swept(analysis),
        _check_ob_at_zone(analysis, current_price),
        _check_fvg_at_zone(analysis, current_price),
        _check_mss_choch(analysis),
        _check_hidden_divergence(analysis),
    ]

    score = sum(1 for item in items if item["status"] == "pass") + \
            sum(0 for item in items if item["status"] == "partial")

    return {"score": score, "max": 7, "items": items}


# ---------------------------------------------------------------------------
# Individual conditions
# ---------------------------------------------------------------------------

def _check_bias(analysis: dict) -> dict:
    bias = analysis.get("bias", {})
    direction = bias.get("direction", "NEUTRAL")
    score_val = bias.get("score", 50)

    if direction in ("BULLISH", "BEARISH"):
        return {
            "name":   "HTF bias established",
            "status": "pass",
            "detail": f"{direction} ({score_val}/100)",
        }
    return {
        "name":   "HTF bias established",
        "status": "fail",
        "detail": f"Bias is NEUTRAL — no clear directional edge",
    }


def _check_premium_discount(analysis: dict, current_price: float) -> dict:
    """
    Price should be in a discount zone for longs (below 50% of daily range)
    or premium zone for shorts (above 50%).
    """
    bias_dir = analysis.get("bias", {}).get("direction", "NEUTRAL")
    daily_df = analysis.get("_daily_df")

    if daily_df is None or len(daily_df) < 20:
        return {
            "name":   "Price in premium/discount zone",
            "status": "partial",
            "detail": "Insufficient daily data for range calculation",
        }

    # Use last 20-day high/low as the range reference
    recent = daily_df.tail(20)
    range_high = float(recent["high"].max())
    range_low  = float(recent["low"].min())
    range_size = range_high - range_low

    if range_size < 1e-9:
        return {"name": "Price in premium/discount zone", "status": "partial", "detail": "Range too narrow"}

    equilibrium = range_low + range_size * 0.5
    discount    = current_price < equilibrium  # below 50% = discount
    premium     = current_price > equilibrium  # above 50% = premium
    pct_of_range = (current_price - range_low) / range_size * 100

    if bias_dir == "BULLISH" and discount:
        return {
            "name":   "Price in premium/discount zone",
            "status": "pass",
            "detail": f"In discount ({pct_of_range:.0f}% of range) — aligned with bullish bias",
        }
    elif bias_dir == "BEARISH" and premium:
        return {
            "name":   "Price in premium/discount zone",
            "status": "pass",
            "detail": f"In premium ({pct_of_range:.0f}% of range) — aligned with bearish bias",
        }
    elif bias_dir == "NEUTRAL":
        return {
            "name":   "Price in premium/discount zone",
            "status": "partial",
            "detail": f"At {pct_of_range:.0f}% of range — no bias for context",
        }
    else:
        return {
            "name":   "Price in premium/discount zone",
            "status": "fail",
            "detail": f"Price in {'premium' if premium else 'discount'} but bias is {bias_dir} — misaligned",
        }


def _check_liquidity_swept(analysis: dict) -> dict:
    """
    Was a liquidity level (EQH/EQL) swept recently?
    We check if any BOS/CHoCH event is recent (within last 5 events).
    """
    bos_choch = analysis.get("bos_choch", [])
    liquidity = analysis.get("liquidity_levels", [])

    if not liquidity:
        return {
            "name":   "Liquidity swept",
            "status": "partial",
            "detail": "No liquidity levels identified",
        }

    # A CHoCH implies a sweep of the prior swing
    recent_choch = [e for e in bos_choch[-10:] if e["type"] == "CHoCH"]
    if recent_choch:
        last = recent_choch[-1]
        return {
            "name":   "Liquidity swept",
            "status": "pass",
            "detail": f"Recent CHoCH at {last['price']:.2f} implies liquidity sweep",
        }

    # Fallback: check if any BOS broke through a known liquidity level
    recent_bos = bos_choch[-5:] if bos_choch else []
    if recent_bos:
        return {
            "name":   "Liquidity swept",
            "status": "partial",
            "detail": f"BOS detected but no confirmed sweep — monitor",
        }

    return {
        "name":   "Liquidity swept",
        "status": "fail",
        "detail": "No recent liquidity sweep detected",
    }


def _check_ob_at_zone(analysis: dict, current_price: float) -> dict:
    """Is price near or inside an unmitigated OB?"""
    obs = analysis.get("order_blocks", [])
    if not obs:
        return {"name": "OB or FVG at entry zone", "status": "fail", "detail": "No order blocks detected"}

    proximity = current_price * 0.005  # 0.5% tolerance

    # Check if price is inside or within 0.5% of any OB
    for ob in obs:
        top    = ob["top"]
        bottom = ob["bottom"]
        in_zone = (bottom - proximity) <= current_price <= (top + proximity)
        if in_zone:
            ob_type = ob["type"].capitalize()
            return {
                "name":   "OB at entry zone",
                "status": "pass",
                "detail": f"{ob_type} OB: {bottom:.2f}–{top:.2f}",
            }

    # Nearest OB
    distances = [min(abs(current_price - ob["top"]), abs(current_price - ob["bottom"])) for ob in obs]
    nearest_idx = int(min(range(len(distances)), key=lambda i: distances[i]))
    nearest = obs[nearest_idx]
    dist_pct = distances[nearest_idx] / current_price * 100

    return {
        "name":   "OB at entry zone",
        "status": "fail",
        "detail": f"Nearest OB ({nearest['type']}) is {dist_pct:.1f}% away",
    }


def _check_fvg_at_zone(analysis: dict, current_price: float) -> dict:
    """Is price near an unmitigated FVG?"""
    fvgs = analysis.get("fvg", [])
    if not fvgs:
        return {"name": "FVG at entry zone", "status": "fail", "detail": "No unmitigated FVGs"}

    proximity = current_price * 0.005

    for fvg in fvgs:
        top    = fvg["top"]
        bottom = fvg["bottom"]
        in_zone = (bottom - proximity) <= current_price <= (top + proximity)
        if in_zone:
            fvg_type = fvg["type"].capitalize()
            return {
                "name":   "FVG at entry zone",
                "status": "pass",
                "detail": f"{fvg_type} FVG: {bottom:.2f}–{top:.2f}",
            }

    distances = [min(abs(current_price - f["top"]), abs(current_price - f["bottom"])) for f in fvgs]
    nearest_idx = int(min(range(len(distances)), key=lambda i: distances[i]))
    nearest = fvgs[nearest_idx]
    dist_pct = distances[nearest_idx] / current_price * 100

    return {
        "name":   "FVG at entry zone",
        "status": "fail",
        "detail": f"Nearest FVG ({nearest['type']}) is {dist_pct:.1f}% away",
    }


def _check_mss_choch(analysis: dict) -> dict:
    """Was there a recent MSS/CHoCH on LTF?"""
    events = analysis.get("bos_choch", [])
    if not events:
        return {
            "name":   "MSS / CHoCH confirmed",
            "status": "fail",
            "detail": "No structure shift detected",
        }

    recent = events[-3:]
    choch_events = [e for e in recent if e["type"] == "CHoCH"]
    bos_events   = [e for e in recent if e["type"] == "BOS"]

    if choch_events:
        last = choch_events[-1]
        return {
            "name":   "MSS / CHoCH confirmed",
            "status": "pass",
            "detail": f"CHoCH ({last['direction']}) at {last['price']:.2f}",
        }
    elif bos_events:
        last = bos_events[-1]
        return {
            "name":   "MSS / CHoCH confirmed",
            "status": "partial",
            "detail": f"BOS only ({last['direction']}) — awaiting CHoCH",
        }

    return {
        "name":   "MSS / CHoCH confirmed",
        "status": "fail",
        "detail": "No recent structure shift",
    }


def _check_hidden_divergence(analysis: dict) -> dict:
    """Is there a confirmed hidden divergence signal?"""
    divs = analysis.get("divergences", [])
    bias_dir = analysis.get("bias", {}).get("direction", "NEUTRAL")

    if not divs:
        return {
            "name":   "Hidden divergence present",
            "status": "fail",
            "detail": "No hidden divergence detected",
        }

    # Check alignment with bias
    aligned = [
        d for d in divs
        if (bias_dir == "BULLISH" and d["type"] == "hidden_bullish") or
           (bias_dir == "BEARISH" and d["type"] == "hidden_bearish")
    ]

    if aligned:
        last = aligned[-1]
        return {
            "name":   "Hidden divergence present",
            "status": "pass",
            "detail": f"{last['label']} aligned with {bias_dir} bias",
        }

    # Divergence present but misaligned
    last = divs[-1]
    return {
        "name":   "Hidden divergence present",
        "status": "partial",
        "detail": f"{last['label']} detected but misaligned with bias",
    }


# ---------------------------------------------------------------------------
# Kill zone check (used by checklist and bias independently)
# ---------------------------------------------------------------------------

def is_kill_zone_active() -> tuple[bool, str]:
    utc = datetime.now(timezone.utc)
    est_h = (utc - timedelta(hours=5)).hour
    if 2 <= est_h < 5:
        return True, "London kill zone"
    if 7 <= est_h < 10:
        return True, "NY kill zone"
    return False, "outside kill zones"
