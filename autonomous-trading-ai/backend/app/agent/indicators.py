import pandas as pd
import numpy as np
from typing import List, Dict, Any
from app.models.market_data import MarketBar

def calculate_technical_indicators(bars: List[MarketBar]) -> Dict[str, Any]:
    """
    Computes key quantitative indicators to assist Gemini with trade decision making:
    - EMA 9, EMA 21 (Trend direction and crossover)
    - RSI 14 (Overbought / Oversold conditions)
    - ATR 14 (Market Volatility for Stop Loss & Take Profit sizing)
    """
    if len(bars) < 14:
        return {}

    df = pd.DataFrame([{
        "timestamp": b.timestamp,
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume
    } for b in bars])

    # 1. EMAs
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()

    # 2. RSI (14)
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss.replace(0, np.nan))
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # 3. ATR (14)
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(window=14).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    trend = "BULLISH" if latest["ema_9"] > latest["ema_21"] else "BEARISH"
    crossover = "BULLISH_CROSS" if (prev["ema_9"] <= prev["ema_21"] and latest["ema_9"] > latest["ema_21"]) else \
                "BEARISH_CROSS" if (prev["ema_9"] >= prev["ema_21"] and latest["ema_9"] < latest["ema_21"]) else "NONE"
    # Retest detection: price within 0.2% of prior swing high or low
    def _is_retest(current_price: float, level: float, tol: float = 0.002) -> bool:
        if level == 0:
            return False
        return abs(current_price - level) / level <= tol
    prev_high = df.iloc[-2]["high"]
    prev_low = df.iloc[-2]["low"]
    current_price = latest["close"]
    retest_valid = _is_retest(current_price, prev_high) or _is_retest(current_price, prev_low)

    return {
        "current_price": float(latest["close"]),
        "ema_9": float(round(latest["ema_9"], 2)) if not pd.isna(latest["ema_9"]) else None,
        "ema_21": float(round(latest["ema_21"], 2)) if not pd.isna(latest["ema_21"]) else None,
        "rsi_14": float(round(latest["rsi_14"], 2)) if not pd.isna(latest["rsi_14"]) else None,
        "atr_14": float(round(latest["atr_14"], 2)) if not pd.isna(latest["atr_14"]) else None,
        "trend": trend,
        "crossover": crossover,
        "retest_valid": bool(retest_valid),
    }
