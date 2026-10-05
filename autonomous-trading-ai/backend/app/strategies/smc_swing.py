import logging
from typing import Dict, Any, Optional

from app.strategies.base import BaseStrategy
from app.agents.swing_trader import SwingTrader
from app.agents.trade_proposal import TradeProposal

logger = logging.getLogger(__name__)

class SMCSwingStrategy(BaseStrategy):
    """
    Institutional Smart Money Concepts (SMC) Swing Strategy:
    - Timeframe Stack: D1 / H4 / H1 / M30 / M15
    - Gated by Alpha Vantage US 10Y Yields
    - Magic Number: 1001
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="SMC_SWING_INSTITUTIONAL", magic_number=1001, enabled=enabled)
        self.trader = SwingTrader()

    def evaluate(self, market_data: Dict[str, Any], symbol: str = "XAUUSD") -> Optional[TradeProposal]:
        if not self.enabled:
            return None

        d1 = market_data.get("1d", [])
        h4 = market_data.get("4h", [])
        h1 = market_data.get("1h", [])
        m30 = market_data.get("30m", [])
        m15 = market_data.get("15m", [])

        # Check standard entry proposal
        prop = self.trader.analyze_and_propose(d1, h4, h1, m30, m15, symbol=symbol)
        if prop:
            return prop

        # Check pullback scale-in
        m5 = market_data.get("5m", [])
        scale_in = self.trader.evaluate_scale_in(m15, m5, h4, h1, symbol=symbol)
        return scale_in
