import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class SMCAnalyzer:
    """
    Smart Money Concepts (SMC) & Institutional Quantitative Analysis:
    - 50 & 200 Exponential Moving Averages (HTF trend & slope)
    - Pivot / Fractal Swing Highs and Lows
    - Break of Structure (BOS) & Change of Character (CHoCH)
    - Order Blocks (OB) & Liquidity Pools
    - Fair Value Gaps (FVG)
    - Asian (00:00-08:00 UTC) & London (07:00-15:00 UTC) Session Extremes & Sweeps
    - Market Structure Shifts (MSS)
    - ATR (14) Volatility
    """

    @staticmethod
    def bars_to_df(bars: List[Any]) -> pd.DataFrame:
        """Convert list of MarketBar objects or dictionaries to pandas DataFrame."""
        if not bars:
            return pd.DataFrame()
            
        data = []
        for b in bars:
            if isinstance(b, dict):
                data.append({
                    "timestamp": b.get("timestamp"),
                    "open": float(b.get("open", 0.0)),
                    "high": float(b.get("high", 0.0)),
                    "low": float(b.get("low", 0.0)),
                    "close": float(b.get("close", 0.0)),
                    "volume": float(b.get("volume", 0.0))
                })
            else:
                data.append({
                    "timestamp": getattr(b, "timestamp", None),
                    "open": float(getattr(b, "open", 0.0)),
                    "high": float(getattr(b, "high", 0.0)),
                    "low": float(getattr(b, "low", 0.0)),
                    "close": float(getattr(b, "close", 0.0)),
                    "volume": float(getattr(b, "volume", 0.0))
                })
                
        df = pd.DataFrame(data)
        if "timestamp" in df.columns and not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.sort_values(by="timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)
        return df

    @classmethod
    def calculate_macro_regime(cls, bars: List[Any]) -> Dict[str, Any]:
        """
        Calculates Macro Regime for H4 / H1:
        - 50 & 200 EMA
        - Trend Direction ('BULLISH', 'BEARISH', 'NEUTRAL')
        - Key Order Blocks & Major Liquidity Pools
        """
        df = cls.bars_to_df(bars)
        if len(df) < 20:
            return {
                "regime": "NEUTRAL",
                "trend": "NEUTRAL",
                "ema_50": None,
                "ema_200": None,
                "key_support": None,
                "key_resistance": None,
                "order_blocks": [],
                "current_price": float(df.iloc[-1]["close"]) if not df.empty else 0.0
            }

        # Calculate EMAs
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
        if len(df) >= 200:
            df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
        else:
            # Fallback if less than 200 bars available
            df["ema_200"] = df["close"].ewm(span=len(df), adjust=False).mean()

        latest = df.iloc[-1]
        current_price = float(latest["close"])
        ema_50 = float(latest["ema_50"])
        ema_200 = float(latest["ema_200"]) if not pd.isna(latest["ema_200"]) else ema_50

        # Trend Determination
        if current_price > ema_50 and ema_50 > ema_200:
            regime = "BULLISH"
        elif current_price < ema_50 and ema_50 < ema_200:
            regime = "BEARISH"
        elif current_price > ema_50:
            regime = "LEAN_BULLISH"
        elif current_price < ema_50:
            regime = "LEAN_BEARISH"
        else:
            regime = "NEUTRAL"

        # Key Highs and Lows (Support & Resistance)
        lookback = min(len(df), 50)
        recent_window = df.tail(lookback)
        key_resistance = float(recent_window["high"].max())
        key_support = float(recent_window["low"].min())

        # Major Order Blocks on HTF
        order_blocks = cls.detect_order_blocks(df, lookback=30)

        return {
            "regime": regime,
            "trend": "BULLISH" if "BULLISH" in regime else ("BEARISH" if "BEARISH" in regime else "NEUTRAL"),
            "ema_50": round(ema_50, 2),
            "ema_200": round(ema_200, 2),
            "key_support": round(key_support, 2),
            "key_resistance": round(key_resistance, 2),
            "order_blocks": order_blocks,
            "current_price": round(current_price, 2)
        }

    @classmethod
    def detect_order_blocks(cls, df: pd.DataFrame, lookback: int = 30) -> List[Dict[str, Any]]:
        """
        Detects Institutional Order Blocks:
        - Bullish OB: Last down-close candle before an upward displacement breaking recent high
        - Bearish OB: Last up-close candle before a downward displacement breaking recent low
        """
        if len(df) < 5:
            return []

        obs = []
        window = df.tail(lookback).copy().reset_index(drop=True)
        
        for i in range(1, len(window) - 2):
            prev_bar = window.iloc[i - 1]
            curr_bar = window.iloc[i]
            next_bar = window.iloc[i + 1]
            follow_bar = window.iloc[i + 2]

            # Bullish displacement: strong green expansion candle
            is_bullish_expansion = (next_bar["close"] > next_bar["open"]) and \
                                   ((next_bar["close"] - next_bar["open"]) > (curr_bar["high"] - curr_bar["low"]))
            # Bearish candle preceding expansion
            if curr_bar["close"] < curr_bar["open"] and is_bullish_expansion:
                if follow_bar["close"] > curr_bar["high"]:
                    obs.append({
                        "type": "BULLISH_OB",
                        "top": round(float(curr_bar["high"]), 2),
                        "bottom": round(float(curr_bar["low"]), 2),
                        "timestamp": str(curr_bar["timestamp"])
                    })

            # Bearish displacement: strong red expansion candle
            is_bearish_expansion = (next_bar["close"] < next_bar["open"]) and \
                                   ((next_bar["open"] - next_bar["close"]) > (curr_bar["high"] - curr_bar["low"]))
            # Bullish candle preceding expansion
            if curr_bar["close"] > curr_bar["open"] and is_bearish_expansion:
                if follow_bar["close"] < curr_bar["low"]:
                    obs.append({
                        "type": "BEARISH_OB",
                        "top": round(float(curr_bar["high"]), 2),
                        "bottom": round(float(curr_bar["low"]), 2),
                        "timestamp": str(curr_bar["timestamp"])
                    })

        return obs[-4:]  # Return most recent 4 order blocks

    @classmethod
    def detect_bos_choch(cls, bars: List[Any], left_bars: int = 3, right_bars: int = 3) -> Dict[str, Any]:
        """
        Detects Break of Structure (BOS) and Change of Character (CHoCH):
        - Fractal swing highs and lows
        - BOS: continuation of trend breaking swing point
        - CHoCH: reversal breaking opposite swing point
        """
        df = cls.bars_to_df(bars)
        if len(df) < (left_bars + right_bars + 5):
            return {
                "bos": None,
                "choch": None,
                "swing_highs": [],
                "swing_lows": [],
                "structure": "INSUFFICIENT_DATA"
            }

        swing_highs = []
        swing_lows = []

        # Find pivot highs and lows
        for i in range(left_bars, len(df) - right_bars):
            high_val = df.iloc[i]["high"]
            low_val = df.iloc[i]["low"]
            
            # Pivot High
            if all(high_val >= df.iloc[i - j]["high"] for j in range(1, left_bars + 1)) and \
               all(high_val > df.iloc[i + j]["high"] for j in range(1, right_bars + 1)):
                swing_highs.append({
                    "index": i,
                    "price": float(high_val),
                    "timestamp": str(df.iloc[i]["timestamp"])
                })

            # Pivot Low
            if all(low_val <= df.iloc[i - j]["low"] for j in range(1, left_bars + 1)) and \
               all(low_val < df.iloc[i + j]["low"] for j in range(1, right_bars + 1)):
                swing_lows.append({
                    "index": i,
                    "price": float(low_val),
                    "timestamp": str(df.iloc[i]["timestamp"])
                })

        latest_close = float(df.iloc[-1]["close"])
        bos = None
        choch = None

        if swing_highs and swing_lows:
            recent_high = swing_highs[-1]["price"]
            recent_low = swing_lows[-1]["price"]
            
            # Bullish Break of Structure: latest close broke above recent swing high
            if latest_close > recent_high:
                bos = {
                    "type": "BULLISH_BOS",
                    "level": recent_high,
                    "break_price": latest_close
                }
            # Bearish Break of Structure: latest close broke below recent swing low
            elif latest_close < recent_low:
                bos = {
                    "type": "BEARISH_BOS",
                    "level": recent_low,
                    "break_price": latest_close
                }

            # Change of Character (reversal breaking secondary swing)
            if len(swing_lows) >= 2 and latest_close < swing_lows[-2]["price"]:
                choch = {
                    "type": "BEARISH_CHOCH",
                    "level": swing_lows[-2]["price"],
                    "current_price": latest_close
                }
            elif len(swing_highs) >= 2 and latest_close > swing_highs[-2]["price"]:
                choch = {
                    "type": "BULLISH_CHOCH",
                    "level": swing_highs[-2]["price"],
                    "current_price": latest_close
                }

        structure = "BULLISH_TREND" if bos and "BULLISH" in bos["type"] else (
            "BEARISH_TREND" if bos and "BEARISH" in bos["type"] else "RANGING"
        )

        return {
            "bos": bos,
            "choch": choch,
            "swing_highs": swing_highs[-3:],
            "swing_lows": swing_lows[-3:],
            "structure": structure
        }

    @classmethod
    def detect_fair_value_gaps(cls, bars: List[Any], lookback: int = 30) -> List[Dict[str, Any]]:
        """
        Detects Fair Value Gaps (FVG) / Imbalances:
        - Bullish FVG: Bar 1 High < Bar 3 Low (Gap = [Bar 1 High, Bar 3 Low])
        - Bearish FVG: Bar 1 Low > Bar 3 High (Gap = [Bar 3 High, Bar 1 Low])
        Checks if gap remains unmitigated by subsequent price action.
        """
        df = cls.bars_to_df(bars)
        if len(df) < 5:
            return []

        fvgs = []
        window = df.tail(lookback).copy().reset_index(drop=True)
        current_close = float(df.iloc[-1]["close"])

        for i in range(len(window) - 2):
            bar1 = window.iloc[i]
            bar2 = window.iloc[i + 1]
            bar3 = window.iloc[i + 2]

            # Bullish FVG
            if bar1["high"] < bar3["low"]:
                gap_low = float(bar1["high"])
                gap_high = float(bar3["low"])
                # Check if mitigated by bars after bar3
                subsequent_bars = window.iloc[i + 3:]
                mitigated = any(subsequent_bars["low"] <= gap_low) if not subsequent_bars.empty else False
                if not mitigated:
                    fvgs.append({
                        "type": "BULLISH_FVG",
                        "gap_low": round(gap_low, 2),
                        "gap_high": round(gap_high, 2),
                        "gap_size": round(gap_high - gap_low, 2),
                        "timestamp": str(bar2["timestamp"]),
                        "distance_to_price": round(current_close - gap_high, 2)
                    })

            # Bearish FVG
            elif bar1["low"] > bar3["high"]:
                gap_high = float(bar1["low"])
                gap_low = float(bar3["high"])
                # Check if mitigated
                subsequent_bars = window.iloc[i + 3:]
                mitigated = any(subsequent_bars["high"] >= gap_high) if not subsequent_bars.empty else False
                if not mitigated:
                    fvgs.append({
                        "type": "BEARISH_FVG",
                        "gap_low": round(gap_low, 2),
                        "gap_high": round(gap_high, 2),
                        "gap_size": round(gap_high - gap_low, 2),
                        "timestamp": str(bar2["timestamp"]),
                        "distance_to_price": round(gap_low - current_close, 2)
                    })

        return fvgs[-4:]  # Return most recent 4 active FVGs

    @classmethod
    def detect_session_liquidity_sweeps(cls, bars: List[Any]) -> Dict[str, Any]:
        """
        Calculates Asian & London Session Extremes & Detects Sweeps:
        - Asian Session: 00:00 - 08:00 UTC
        - London Session: 07:00 - 15:00 UTC
        - Sweep: Price breaks above High or below Low with a wick and re-enters the range.
        """
        df = cls.bars_to_df(bars)
        if df.empty:
            return {"asian_sweep": None, "london_sweep": None, "asian_high": None, "asian_low": None}

        # Ensure timestamp is UTC
        if df["timestamp"].dt.tz is None:
            df["utc_time"] = df["timestamp"].dt.tz_localize("UTC")
        else:
            df["utc_time"] = df["timestamp"].dt.tz_convert("UTC")

        today_utc = datetime.now(timezone.utc).date()
        
        # Filter for today's bars (or recent 24 hours)
        today_bars = df[df["utc_time"].dt.date == today_utc]
        if today_bars.empty:
            today_bars = df.tail(50)

        # Asian Session (00:00 to 08:00 UTC)
        asian_bars = today_bars[(today_bars["utc_time"].dt.hour >= 0) & (today_bars["utc_time"].dt.hour < 8)]
        asian_high = float(asian_bars["high"].max()) if not asian_bars.empty else None
        asian_low = float(asian_bars["low"].min()) if not asian_bars.empty else None

        # London Session (07:00 to 15:00 UTC)
        london_bars = today_bars[(today_bars["utc_time"].dt.hour >= 7) & (today_bars["utc_time"].dt.hour < 15)]
        london_high = float(london_bars["high"].max()) if not london_bars.empty else None
        london_low = float(london_bars["low"].min()) if not london_bars.empty else None

        latest_bar = df.iloc[-1]
        current_close = float(latest_bar["close"])
        current_high = float(latest_bar["high"])
        current_low = float(latest_bar["low"])

        asian_sweep = None
        if asian_high and current_high > asian_high and current_close < asian_high:
            asian_sweep = {
                "type": "BEARISH_ASIAN_HIGH_SWEEP",
                "swept_level": round(asian_high, 2),
                "sweep_wick_extreme": round(current_high, 2),
                "invalidation": round(current_high + 0.50, 2)  # Invalidation above sweep wick
            }
        elif asian_low and current_low < asian_low and current_close > asian_low:
            asian_sweep = {
                "type": "BULLISH_ASIAN_LOW_SWEEP",
                "swept_level": round(asian_low, 2),
                "sweep_wick_extreme": round(current_low, 2),
                "invalidation": round(current_low - 0.50, 2)  # Invalidation below sweep wick
            }

        london_sweep = None
        if london_high and current_high > london_high and current_close < london_high:
            london_sweep = {
                "type": "BEARISH_LONDON_HIGH_SWEEP",
                "swept_level": round(london_high, 2),
                "sweep_wick_extreme": round(current_high, 2),
                "invalidation": round(current_high + 0.50, 2)
            }
        elif london_low and current_low < london_low and current_close > london_low:
            london_sweep = {
                "type": "BULLISH_LONDON_LOW_SWEEP",
                "swept_level": round(london_low, 2),
                "sweep_wick_extreme": round(current_low, 2),
                "invalidation": round(current_low - 0.50, 2)
            }

        return {
            "asian_high": round(asian_high, 2) if asian_high else None,
            "asian_low": round(asian_low, 2) if asian_low else None,
            "london_high": round(london_high, 2) if london_high else None,
            "london_low": round(london_low, 2) if london_low else None,
            "asian_sweep": asian_sweep,
            "london_sweep": london_sweep
        }

    @classmethod
    def calculate_atr(cls, bars: List[Any], period: int = 14) -> float:
        """Calculates Average True Range (ATR)."""
        df = cls.bars_to_df(bars)
        if len(df) < period + 1:
            return 2.0  # Default reasonable ATR on Gold

        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_series = tr.rolling(window=period).mean()
        latest_atr = atr_series.iloc[-1]
        return float(round(latest_atr, 2)) if not pd.isna(latest_atr) else 2.0
