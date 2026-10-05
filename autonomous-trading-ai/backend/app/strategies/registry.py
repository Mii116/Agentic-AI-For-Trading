import logging
from typing import Dict, List, Optional, Any

from app.strategies.base import BaseStrategy
from app.strategies.smc_swing import SMCSwingStrategy
from app.strategies.session_scalp import SessionScalpStrategy
from app.agents.trade_proposal import TradeProposal

logger = logging.getLogger(__name__)

class StrategyRegistry:
    """
    Central Strategy Registry:
    - Maintains all registered algorithmic strategies.
    - Allows dynamic runtime enabling/disabling of individual strategies.
    - Gathers proposals concurrently from all active strategies.
    """

    def __init__(self):
        self._strategies: Dict[str, BaseStrategy] = {}
        # Register default strategies
        self.register(SMCSwingStrategy())
        self.register(SessionScalpStrategy())

    def register(self, strategy: BaseStrategy):
        self._strategies[strategy.name] = strategy
        logger.info(f"[StrategyRegistry] Registered strategy: {strategy.name} (Magic: {strategy.magic_number})")

    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        return self._strategies.get(name)

    def list_strategies(self) -> List[Dict[str, Any]]:
        return [s.get_info() for s in self._strategies.values()]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        strat = self._strategies.get(name)
        if strat:
            strat.enabled = enabled
            logger.info(f"[StrategyRegistry] Set {name} enabled={enabled}")
            return True
        return False

    def evaluate_all(self, market_data: Dict[str, Any], symbol: str = "XAUUSD") -> List[TradeProposal]:
        """Runs evaluation across all active registered strategies and returns candidate proposals."""
        proposals = []
        for strat in self._strategies.values():
            if not strat.enabled:
                continue
            try:
                prop = strat.evaluate(market_data, symbol=symbol)
                if prop:
                    proposals.append(prop)
            except Exception as e:
                logger.error(f"[StrategyRegistry] Error evaluating {strat.name}: {e}")
        return proposals
