import logging
from typing import Dict, Any, List, Tuple

from app.portfolio.manager import PortfolioManager

logger = logging.getLogger(__name__)

class PortfolioRebalancer:
    """
    Portfolio Risk Rebalancer:
    - Analyzes target vs actual asset weights.
    - Flags portfolio concentration breaches (e.g. Gold exposure exceeding risk limits).
    - Suggests lot adjustments or position reductions to re-align risk budgets.
    """

    def __init__(self, target_weights: Dict[str, float] = None):
        # Default allocation model: 70% Gold, 30% Crypto
        self.target_weights = target_weights or {
            "XAUUSD": 0.70,
            "BTCUSD": 0.30
        }
        self.manager = PortfolioManager()

    def check_rebalance_triggers(self) -> Tuple[bool, List[str]]:
        """Evaluates whether current portfolio exposures breach institutional risk limits."""
        summary = self.manager.get_portfolio_summary()
        assets = summary.get("assets", {})
        equity = summary.get("equity", 0.0)

        if equity <= 0 or not assets:
            return False, ["Portfolio flat or zero equity."]

        triggers = []
        needs_rebalance = False

        # 1. Check Gross Leverage
        if summary.get("effective_leverage", 0.0) > 10.0:
            needs_rebalance = True
            triggers.append(f"LEVERAGE WARNING: Effective leverage ({summary['effective_leverage']:.1f}x) exceeds 10x ceiling.")

        # 2. Check Individual Asset Concentration
        for sym, data in assets.items():
            weight = data.get("weight_pct", 0.0)
            if weight > 100.0:
                needs_rebalance = True
                triggers.append(f"CONCENTRATION WARNING: {sym} notional exposure ({weight:.1f}%) exceeds 100% of equity.")

        return needs_rebalance, triggers
