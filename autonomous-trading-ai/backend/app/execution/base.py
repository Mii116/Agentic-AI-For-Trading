from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseExecutionRouter(ABC):
    """Abstract interface defining the execution protocol for all brokers and simulators."""

    @abstractmethod
    def execute_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "",
        magic_number: int = 1001,
        deviation: int = 25
    ) -> bool:
        """Executes an institutional order with explicit risk and slippage controls."""
        pass

    @abstractmethod
    def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        """Closes a position in full or partially."""
        pass

    @abstractmethod
    def modify_sl_tp(self, ticket: int, stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> bool:
        """Modifies SL/TP on an active position."""
        pass

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns open positions segregated by ticket and magic number."""
        pass

    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        """Returns equity, balance, margin used, and free margin."""
        pass
