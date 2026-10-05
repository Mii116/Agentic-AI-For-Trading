from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from app.agents.trade_proposal import TradeProposal

class BaseStrategy(ABC):
    """Abstract interface for all algorithmic trading strategies."""

    def __init__(self, name: str, magic_number: int, enabled: bool = True):
        self.name = name
        self.magic_number = magic_number
        self.enabled = enabled

    @abstractmethod
    def evaluate(self, market_data: Dict[str, Any], symbol: str = "XAUUSD") -> Optional[TradeProposal]:
        """Evaluates price data and indicators to generate candidate TradeProposals."""
        pass

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "magic_number": self.magic_number,
            "enabled": self.enabled
        }
