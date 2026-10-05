import logging
from typing import Dict, Any, List, Optional
import MetaTrader5 as mt5

from app.data.mt5_data import MT5DataClient
from app.trading.macro_worker import AlphaVantageMacroWorker
from app.indicators.smc import SMCAnalyzer

logger = logging.getLogger(__name__)

class MarketDataService:
    """
    Market Data Aggregation Service:
    - Provides live bid/ask/spread metrics for target assets
    - Fetches multi-timeframe candles (D1, H4, H1, 30m, 15m, 5m, 1m)
    - Provides real-time SMC technical analysis (EMAs, Support/Resistance, Order Blocks, FVGs)
    - Interfaces with Alpha Vantage Macro Caching Worker for US 10Y Yields
    """

    def __init__(self):
        self.data_client = MT5DataClient()
        self.macro_worker = AlphaVantageMacroWorker()

    def get_live_quote(self, symbol: str = "XAUUSD") -> Dict[str, Any]:
        """Returns real-time bid, ask, spread, and timestamp."""
        if not mt5.initialize():
            return {"error": "MT5 offline"}

        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if not tick or not info:
            return {"error": f"Symbol {symbol} unavailable"}

        point = info.point or 0.01
        spread_pts = round((tick.ask - tick.bid) / point, 1)

        return {
            "symbol": symbol,
            "bid": float(tick.bid),
            "ask": float(tick.ask),
            "spread_points": spread_pts,
            "spread_usd": round(spread_pts * point, 2),
            "time": tick.time
        }

    def get_timeframe_stack(self, symbol: str = "XAUUSD") -> Dict[str, List[Any]]:
        """Fetches candles across timeframe stack."""
        return {
            "1d": self.data_client.fetch_bars(symbol, "1d", 30),
            "4h": self.data_client.fetch_bars(symbol, "4h", 40),
            "1h": self.data_client.fetch_bars(symbol, "1h", 40),
            "30m": self.data_client.fetch_bars(symbol, "30m", 40),
            "15m": self.data_client.fetch_bars(symbol, "15m", 40),
            "5m": self.data_client.fetch_bars(symbol, "5m", 40),
            "1m": self.data_client.fetch_bars(symbol, "1m", 40)
        }

    def get_smc_analysis(self, symbol: str = "XAUUSD", timeframe: str = "1h") -> Dict[str, Any]:
        """Computes institutional SMC analysis for the specified timeframe."""
        bars = self.data_client.fetch_bars(symbol, timeframe, 50)
        if not bars:
            return {"error": "Insufficient bar data"}

        regime = SMCAnalyzer.calculate_macro_regime(bars)
        bos_data = SMCAnalyzer.detect_bos_choch(bars)
        fvgs = SMCAnalyzer.detect_fair_value_gaps(bars)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "regime": regime,
            "bos": bos_data.get("bos"),
            "choch": bos_data.get("choch"),
            "fair_value_gaps": fvgs[-3:] if fvgs else []
        }

    def get_macro_state(self) -> Dict[str, Any]:
        return self.macro_worker.get_macro_state()
