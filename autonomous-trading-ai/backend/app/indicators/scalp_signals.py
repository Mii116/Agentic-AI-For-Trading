"""
Deterministic, vectorized signal primitives for the Scalp Trader (Magic 2002).

Design rules (production quant standard):
- ZERO network / LLM calls. Every function is pure numpy and runs in < 1 ms.
- CLOSED BARS ONLY. The currently forming candle is never used for setup
  detection; a forming bar can print a "sweep" that is erased seconds later.
- TRUE UTC TIMESTAMPS. MT5 returns bar/tick times as *broker server* wall-clock
  epochs (often UTC+2/UTC+3). We measure the broker offset from the live tick
  and subtract it, so Asian/London session windows are evaluated correctly.
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

_OFFSET_CACHE: Dict[str, Any] = {"offset": 0, "computed_at": 0.0, "valid": False}
_OFFSET_TTL_SEC = 600.0


# ---------------------------------------------------------------------------
# Broker clock handling
# ---------------------------------------------------------------------------
def server_utc_offset(symbol: str = "XAUUSD") -> int:
    """
    Broker server offset from UTC in seconds (rounded to 30 min).
    MT5 tick.time is the server wall-clock expressed as an epoch, so
    offset = tick.time - real_utc_epoch. Cached for 10 minutes. Falls back to
    the last known value (or 0) when the market is closed / tick is stale.
    """
    now = time.time()
    if _OFFSET_CACHE["valid"] and (now - _OFFSET_CACHE["computed_at"]) < _OFFSET_TTL_SEC:
        return int(_OFFSET_CACHE["offset"])
    try:
        tick = mt5.symbol_info_tick(symbol)
        if tick and tick.time > 0:
            raw = float(tick.time) - now
            offset = int(round(raw / 1800.0) * 1800)
            # Plausible broker offsets are within +/-14h; a larger value means a stale tick.
            if abs(offset) <= 14 * 3600 and abs(raw - offset) < 900:
                _OFFSET_CACHE.update({"offset": offset, "computed_at": now, "valid": True})
                return offset
    except Exception as e:  # pragma: no cover - MT5 not connected
        logger.debug(f"server_utc_offset failed: {e}")
    return int(_OFFSET_CACHE["offset"])


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def fetch_closed_rates(symbol: str, timeframe: int, count: int) -> Optional[Dict[str, np.ndarray]]:
    """
    Fetches `count` CLOSED bars (start_pos=1 skips the forming bar) with true-UTC
    epoch timestamps. Returns dict of numpy arrays or None.
    """
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None or len(rates) == 0:
        return None
    offset = server_utc_offset(symbol)
    return {
        "time": rates["time"].astype(np.int64) - offset,
        "open": rates["open"].astype(np.float64),
        "high": rates["high"].astype(np.float64),
        "low": rates["low"].astype(np.float64),
        "close": rates["close"].astype(np.float64),
        "volume": rates["tick_volume"].astype(np.float64),
    }


def bars_to_arrays(bars: List[Any], drop_last: bool = True) -> Optional[Dict[str, np.ndarray]]:
    """
    Fallback converter for list-of-dict bars (e.g. MT5DataClient.fetch_bars output).
    `drop_last=True` removes the forming bar. Timestamps are best-effort only.
    """
    if not bars:
        return None
    src = bars[:-1] if drop_last and len(bars) > 1 else bars

    def g(b, k, default=0.0):
        return b.get(k, default) if isinstance(b, dict) else getattr(b, k, default)

    ts = []
    for b in src:
        t = g(b, "timestamp", None)
        if isinstance(t, datetime):
            ts.append(int(t.replace(tzinfo=t.tzinfo or timezone.utc).timestamp()))
        else:
            ts.append(0)
    return {
        "time": np.array(ts, dtype=np.int64),
        "open": np.array([float(g(b, "open")) for b in src]),
        "high": np.array([float(g(b, "high")) for b in src]),
        "low": np.array([float(g(b, "low")) for b in src]),
        "close": np.array([float(g(b, "close")) for b in src]),
        "volume": np.array([float(g(b, "volume")) for b in src]),
    }


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------
def atr(a: Dict[str, np.ndarray], period: int = 14) -> float:
    """Simple-mean ATR over the last `period` closed bars (matches SMCAnalyzer)."""
    h, l, c = a["high"], a["low"], a["close"]
    if len(c) < period + 1:
        return 2.0
    prev_c = c[:-1]
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c)])
    return float(round(tr[-period:].mean(), 2))


def pivot_swings(a: Dict[str, np.ndarray], left: int = 2, right: int = 2) -> Dict[str, List[Dict[str, float]]]:
    """Confirmed fractal swing highs/lows (vectorized sliding window)."""
    h, l = a["high"], a["low"]
    n = len(h)
    out = {"highs": [], "lows": []}
    if n < left + right + 1:
        return out
    win = left + right + 1
    hw = np.lib.stride_tricks.sliding_window_view(h, win)
    lw = np.lib.stride_tricks.sliding_window_view(l, win)
    centre_h = hw[:, left]
    centre_l = lw[:, left]
    is_sh = (centre_h >= hw[:, :left].max(axis=1)) & (centre_h > hw[:, left + 1:].max(axis=1))
    is_sl = (centre_l <= lw[:, :left].min(axis=1)) & (centre_l < lw[:, left + 1:].min(axis=1))
    for i in np.nonzero(is_sh)[0]:
        out["highs"].append({"index": int(i + left), "price": float(centre_h[i]), "time": int(a["time"][i + left])})
    for i in np.nonzero(is_sl)[0]:
        out["lows"].append({"index": int(i + left), "price": float(centre_l[i]), "time": int(a["time"][i + left])})
    return out


# ---------------------------------------------------------------------------
# Liquidity levels & sweeps
# ---------------------------------------------------------------------------
def session_levels(a: Dict[str, np.ndarray], now_utc: Optional[datetime] = None) -> Dict[str, float]:
    """
    Liquidity pools from COMPLETED ranges only (so a level can never contain
    the bar that sweeps it):
    - PDH / PDL : previous UTC day high/low
    - ASIA_H / ASIA_L : today 00:00-07:00 UTC (valid from 07:00)
    - LDN_H / LDN_L  : today 07:00-12:00 UTC (valid from 12:00, for NY session)
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    t, h, l = a["time"], a["high"], a["low"]
    day0 = int(datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc).timestamp())
    levels: Dict[str, float] = {}

    def rng(start: int, end: int, hk: str, lk: str):
        m = (t >= start) & (t < end)
        if m.any():
            levels[hk] = float(h[m].max())
            levels[lk] = float(l[m].min())

    # Previous trading day (skip weekend gaps by walking back up to 3 days)
    for back in range(1, 4):
        start = day0 - back * 86400
        m = (t >= start) & (t < start + 86400)
        if m.any():
            levels["PDH"] = float(h[m].max())
            levels["PDL"] = float(l[m].min())
            break

    if now_utc.hour >= 7:
        rng(day0, day0 + 7 * 3600, "ASIA_H", "ASIA_L")
    if now_utc.hour >= 12:
        rng(day0 + 7 * 3600, day0 + 12 * 3600, "LDN_H", "LDN_L")
    return levels


def detect_sweep(a: Dict[str, np.ndarray], levels: Dict[str, float], atr_val: float,
                 lookback: int = 3, min_wick_ratio: float = 0.40) -> Optional[Dict[str, Any]]:
    """
    Most recent liquidity sweep among the last `lookback` CLOSED bars.
    Bearish sweep: high pierces a *_H/PDH level by >= max(0.10, 0.05*ATR) and closes back below,
                   with an upper wick >= 40% of the candle range.
    Bullish sweep: mirror on *_L/PDL levels.
    Sweep stays valid only if no later closed bar has broken its extreme.
    """
    h, l, o, c = a["high"], a["low"], a["open"], a["close"]
    n = len(c)
    if n < lookback + 2 or not levels:
        return None
    min_pierce = max(0.10, 0.05 * atr_val)

    for i in range(n - 1, n - 1 - lookback, -1):
        rng_ = h[i] - l[i]
        if rng_ <= 0:
            continue
        later_h = h[i + 1:].max() if i + 1 < n else -np.inf
        later_l = l[i + 1:].min() if i + 1 < n else np.inf
        upper_wick = (h[i] - max(o[i], c[i])) / rng_
        lower_wick = (min(o[i], c[i]) - l[i]) / rng_

        for name, lvl in levels.items():
            if name.endswith("H") and (h[i] - lvl) >= min_pierce and c[i] < lvl \
                    and upper_wick >= min_wick_ratio and later_h <= h[i]:
                return {"direction": "SELL", "level_name": name, "level": round(lvl, 2),
                        "extreme": round(float(h[i]), 2), "bar_index": i, "bar_time": int(a["time"][i])}
            if name.endswith("L") and (lvl - l[i]) >= min_pierce and c[i] > lvl \
                    and lower_wick >= min_wick_ratio and later_l >= l[i]:
                return {"direction": "BUY", "level_name": name, "level": round(lvl, 2),
                        "extreme": round(float(l[i]), 2), "bar_index": i, "bar_time": int(a["time"][i])}
    return None


# ---------------------------------------------------------------------------
# Fair Value Gaps
# ---------------------------------------------------------------------------
def detect_fresh_fvgs(a: Dict[str, np.ndarray], atr_val: float, lookback: int = 30,
                      min_gap_atr: float = 0.25, min_body_atr: float = 0.80) -> List[Dict[str, Any]]:
    """
    Vectorized FVG scan over CLOSED bars. Keeps only institutional-grade gaps:
    - gap size >= 0.25 x ATR (filters micro-noise)
    - displacement candle body >= 0.80 x ATR
    - FRESH: price has not yet traded back into the gap (first touch only)
    Returns newest-last list.
    """
    h, l, o, c = a["high"], a["low"], a["open"], a["close"]
    n = len(c)
    if n < 5:
        return []
    start = max(0, n - lookback)
    i1 = np.arange(start, n - 2)
    i2, i3 = i1 + 1, i1 + 2
    body = np.abs(c[i2] - o[i2])

    bull = (h[i1] < l[i3]) & ((l[i3] - h[i1]) >= min_gap_atr * atr_val) & (body >= min_body_atr * atr_val) & (c[i2] > o[i2])
    bear = (l[i1] > h[i3]) & ((l[i1] - h[i3]) >= min_gap_atr * atr_val) & (body >= min_body_atr * atr_val) & (c[i2] < o[i2])

    out: List[Dict[str, Any]] = []
    for k in np.nonzero(bull | bear)[0]:
        a1, a3 = int(i1[k]), int(i3[k])
        after = slice(a3 + 1, n)
        if bull[k]:
            gl, gh = float(h[a1]), float(l[a3])
            if a3 + 1 < n and l[after].min() <= gh:
                continue  # already touched
            out.append({"direction": "BUY", "gap_low": round(gl, 2), "gap_high": round(gh, 2),
                        "bar_index": a3, "bar_time": int(a["time"][a3])})
        else:
            gl, gh = float(h[a3]), float(l[a1])
            if a3 + 1 < n and h[after].max() >= gl:
                continue
            out.append({"direction": "SELL", "gap_low": round(gl, 2), "gap_high": round(gh, 2),
                        "bar_index": a3, "bar_time": int(a["time"][a3])})
    return out
