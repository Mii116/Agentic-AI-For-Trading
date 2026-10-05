import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import MetaTrader5 as mt5

from app.execution.order_router import OrderRouter
from app.db.session import SessionLocal
from app.models.trading import TradeJournal, Position
from app.schemas.trade import TradeCreate
from app.trading.danger_sentry import DangerSentry

logger = logging.getLogger(__name__)

class TradingService:
    """
    Central Trading Business Logic Service:
    - Coordinates order submission through OrderRouter
    - Coordinates manual emergency closes through DangerSentry
    - Updates TradeJournal records and dispatches AI post-mortem when appropriate
    """

    def __init__(self, router: Optional[OrderRouter] = None):
        self.router = router or OrderRouter()
        self.danger_sentry = DangerSentry(execution_engine=self.router.engine)

    def submit_trade(self, req: TradeCreate) -> Dict[str, Any]:
        """Submits an institutional order with safety gates."""
        success = self.router.execute_order(
            symbol=req.symbol,
            side=req.side,
            volume=req.lot_size,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            comment=f"{req.technique_used[:15] if req.technique_used else 'API'}",
            magic_number=req.magic_number,
            deviation=25
        )

        return {
            "success": success,
            "symbol": req.symbol,
            "side": req.side,
            "volume": req.lot_size,
            "magic_number": req.magic_number,
            "message": "Order successfully routed to execution engine." if success else "Order routing rejected."
        }

    def close_position_by_ticket(self, ticket: int, volume: Optional[float] = None) -> bool:
        """Closes a position in full or partially."""
        return self.router.close_position(ticket, volume=volume)

    def emergency_close_all(self, symbol: Optional[str] = "XAUUSD") -> int:
        """Emergency panic liquidation of all active positions on symbol."""
        positions = self.router.get_positions(symbol=symbol)
        closed_count = 0
        for p in positions:
            if self.router.close_position(p["ticket"]):
                closed_count += 1
        logger.warning(f"[TradingService] Emergency close triggered: liquidated {closed_count} positions.")
        return closed_count

    def get_active_positions(self, symbol: Optional[str] = "XAUUSD") -> List[Dict[str, Any]]:
        return self.router.get_positions(symbol=symbol)

    def get_account_summary(self) -> Dict[str, Any]:
        return self.router.get_account_info()
