import logging
from typing import Dict, Any, Optional

from app.strategies.base import BaseStrategy
from app.agents.scalp_trader import ScalpTrader
from app.agents.trade_proposal import TradeProposal

logger = logging.getLogger(__name__)

class SessionScalpStrategy(BaseStrategy):
    """
    Session Liquidity Sweep & Fair Value Gap (FVG) Scalp Strategy:
    - Timeframe Stack: 5m / 1m
    - London & New York session filter (07:00 - 17:00 UTC)
    - Magic Number: 2002
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="SESSION_SCALP_SNIPER", magic_number=2002, enabled=enabled)
        self.trader = ScalpTrader()

    def evaluate(self, market_data: Dict[str, Any], symbol: str = "XAUUSD") -> Optional[TradeProposal]:
        if not self.enabled:
            return None

        m5 = market_data.get("5m", [])
        m1 = market_data.get("1m", [])

        return self.trader.analyze_and_propose(m5, m1, symbol=symbol)
