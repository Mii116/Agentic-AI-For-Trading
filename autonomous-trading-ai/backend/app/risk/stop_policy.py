"""
Volatility-Adjusted Stop Policy for XAUUSD (Gold).

Single source of truth for stop-distance rules shared by the Scalp Trader,
Chief Risk Arbiter, Zone-Retest Monitor and Danger Sentry.

    SL Distance = max(structural distance, 1.5 x ATR(14, M5), $2.00)
    Hard floor  = $1.50 for ANY Gold stop (swing or scalp), so routine
                  bid/ask spread fluctuations (25-45 pts) can never consume
                  the majority of the stop.
"""
from typing import Tuple

MIN_STOP_DISTANCE = 1.50   # Absolute floor ($) for any stop on Gold
SCALP_STOP_FLOOR = 2.00    # Scalper volatility floor ($)
SCALP_ATR_MULT = 1.5       # Stop >= 1.5 x ATR(14, M5)


def scalp_stop_distance(structural_distance: float, atr_m5: float) -> float:
    """Returns the volatility-adjusted stop distance for a scalp entry."""
    return round(max(
        float(structural_distance or 0.0),
        SCALP_ATR_MULT * float(atr_m5 or 0.0),
        SCALP_STOP_FLOOR,
        MIN_STOP_DISTANCE,
    ), 2)


def enforce_min_stop(direction: str, entry: float, sl: float,
                     min_dist: float = MIN_STOP_DISTANCE) -> Tuple[float, bool]:
    """
    Widens a stop that is tighter than `min_dist` from entry.
    Returns (adjusted_sl, was_widened). Stops of 0.0 (none) are left untouched.
    """
    if not sl or sl <= 0 or not entry:
        return sl, False
    if direction.upper() == "BUY":
        if entry - sl < min_dist:
            return round(entry - min_dist, 2), True
    elif direction.upper() == "SELL":
        if sl - entry < min_dist:
            return round(entry + min_dist, 2), True
    return sl, False
