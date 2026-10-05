import logging
import MetaTrader5 as mt5
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.models.market_data import MarketBar
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

class MT5DataClient:
    def __init__(self):
        # We assume MT5 is already initialized by the execution engine, 
        # but we can call initialize just in case (it's safe to call multiple times).
        terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
        if not mt5.initialize(path=terminal_path):
            logger.error(f"MT5DataClient failed to initialize MT5: {mt5.last_error()}")

    def _map_timeframe(self, tf_string: str):
        tf_lower = str(tf_string).lower()
        mapping = {
            "1m": mt5.TIMEFRAME_M1,
            "m1": mt5.TIMEFRAME_M1,
            "5m": mt5.TIMEFRAME_M5,
            "m5": mt5.TIMEFRAME_M5,
            "15m": mt5.TIMEFRAME_M15,
            "m15": mt5.TIMEFRAME_M15,
            "30m": mt5.TIMEFRAME_M30,
            "m30": mt5.TIMEFRAME_M30,
            "1h": mt5.TIMEFRAME_H1,
            "h1": mt5.TIMEFRAME_H1,
            "4h": mt5.TIMEFRAME_H4,
            "h4": mt5.TIMEFRAME_H4,
            "1d": mt5.TIMEFRAME_D1,
            "d1": mt5.TIMEFRAME_D1
        }
        return mapping.get(tf_lower, mt5.TIMEFRAME_M1)

    def fetch_multi_timeframe_bars(self, symbol: str, timeframes: List[str] = None, num_bars: int = 100) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch bars across multiple timeframes for multi-agent analysis."""
        if timeframes is None:
            timeframes = ["4h", "1h", "15m", "5m", "1m"]
        result = {}
        for tf in timeframes:
            bars = self.fetch_bars(symbol=symbol, timeframe=tf, num_bars=num_bars)
            result[tf] = bars
        return result

    def fetch_bars(self, symbol: str, timeframe: str = "1m", num_bars: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch historical OHLCV bars directly from MT5.
        timeframe: "1m", "5m", "15m", "1h", "1d"
        """
        mt5_tf = self._map_timeframe(timeframe)
        
        # Ensure symbol is visible
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Failed to select symbol {symbol} in MT5")
            return []

        # Request rates
        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, num_bars)
        if rates is None or len(rates) == 0:
            logger.error(f"Failed to fetch rates for {symbol} on {timeframe}")
            return []

        bars = []
        for rate in rates:
            # rate is a tuple: (time, open, high, low, close, tick_volume, spread, real_volume)
            dt = datetime.fromtimestamp(rate[0])
            bars.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": dt,
                "open": float(rate[1]),
                "high": float(rate[2]),
                "low": float(rate[3]),
                "close": float(rate[4]),
                "volume": float(rate[5])  # Using tick_volume
            })
            
        # Ensure sorted ascending
        bars.sort(key=lambda x: x["timestamp"])
        return bars

    def save_bars_to_db(self, bars: List[Dict[str, Any]]) -> int:
        """Upsert market bars into local database."""
        if not bars:
            return 0

        db = SessionLocal()
        saved_count = 0
        try:
            for bar_data in bars:
                existing = db.query(MarketBar).filter_by(
                    symbol=bar_data["symbol"],
                    timeframe=bar_data["timeframe"],
                    timestamp=bar_data["timestamp"]
                ).first()

                if not existing:
                    bar = MarketBar(**bar_data)
                    db.add(bar)
                    saved_count += 1
            db.commit()
            logger.info(f"Saved {saved_count} new {bars[0]['timeframe']} bars for {bars[0]['symbol']} to database.")
            return saved_count
        except Exception as e:
            db.rollback()
            logger.error(f"Error saving bars to db: {e}")
            return 0
        finally:
            db.close()
