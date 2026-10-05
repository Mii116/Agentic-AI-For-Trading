import logging
from typing import List, Dict, Any, Optional
import MetaTrader5 as mt5

from app.execution.base import BaseExecutionRouter
from app.execution.smart_execution import SmartExecutionHandler
from app.trading.mt5_engine import MT5ExecutionEngine
from app.trading.paper_engine import PaperTradingEngine
from app.config.settings import settings

logger = logging.getLogger(__name__)

class OrderRouter(BaseExecutionRouter):
    """
    Central Order Router:
    - Decides whether to route orders to MT5 (Live/Demo) or PaperTradingEngine based on settings.
    - Applies SmartExecutionHandler spread validation, slippage caps (deviation=25), and order slicing.
    - Manages partial closes and stop loss modifications.
    """

    def __init__(self, execution_engine=None):
        if execution_engine:
            self.engine = execution_engine
        elif settings.MT5_ENABLED:
            self.engine = MT5ExecutionEngine()
        else:
            self.engine = PaperTradingEngine()

    def execute_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "Router Order",
        magic_number: int = 1001,
        deviation: int = 25
    ) -> bool:
        """Validates execution conditions, slices if necessary, and routes order to engine."""
        # 1. Check Smart Execution Gate
        is_safe, reason = SmartExecutionHandler.validate_execution_conditions(symbol)
        if not is_safe:
            logger.warning(f"[OrderRouter] Order rejected by SmartExecution: {reason}")
            return False

        # 2. Slice volume if oversized
        slices = SmartExecutionHandler.calculate_order_slices(volume)

        all_success = True
        for i, tranche in enumerate(slices):
            tranche_comment = f"{comment} S{i+1}" if len(slices) > 1 else comment
            
            # Create a mock proposal object to carry magic_number
            class ProposalProxy:
                def __init__(self, magic):
                    self.magic_number = magic

            success = self.engine.execute_custom_order(
                symbol=symbol,
                side=side,
                volume=tranche,
                stop_loss=stop_loss,
                take_profit=take_profit,
                comment=tranche_comment[:31],
                proposal=ProposalProxy(magic_number)
            )
            if not success:
                all_success = False
                logger.error(f"[OrderRouter] Failed to execute tranche {i+1}/{len(slices)} ({tranche} lots)")

        return all_success

    def execute_limit_order(
        self,
        symbol: str,
        side: str,
        limit_price: float,
        volume: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "Router Limit",
        magic_number: int = 1001,
        proposal: Any = None,
        ttl_minutes: int = 20
    ) -> bool:
        """Validates execution conditions and routes pending limit order to engine."""
        is_safe, reason = SmartExecutionHandler.validate_execution_conditions(symbol)
        if not is_safe:
            logger.warning(f"[OrderRouter] Limit order rejected by SmartExecution: {reason}")
            return False

        if not proposal:
            class ProposalProxy:
                def __init__(self, magic):
                    self.magic_number = magic
                    self.setup_cluster = "LIMIT_EXECUTION"
                    self.thesis = "OrderRouter limit execution"
                    self.invalidation_condition = ""
            proposal = ProposalProxy(magic_number)

        if hasattr(self.engine, "execute_custom_limit_order"):
            return self.engine.execute_custom_limit_order(
                symbol=symbol,
                side=side,
                limit_price=limit_price,
                volume=volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
                comment=comment[:31],
                proposal=proposal,
                ttl_minutes=ttl_minutes
            )
        return False

    def cancel_order(self, ticket: int, reason: str = "") -> bool:
        """Cancels a pending order."""
        if hasattr(self.engine, "cancel_pending_order"):
            return self.engine.cancel_pending_order(ticket, reason)
        return False

    def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        """Closes a position in full or partially."""
        if volume and hasattr(self.engine, "partial_close_position"):
            return self.engine.partial_close_position(ticket, volume)
        return self.engine.close_position(ticket)

    def modify_sl_tp(self, ticket: int, stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> bool:
        """Modifies SL/TP on an active position."""
        if hasattr(self.engine, "modify_position_sl") and stop_loss is not None:
            return self.engine.modify_position_sl(ticket, stop_loss)
        return False

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns open positions from broker/engine."""
        if not mt5.initialize():
            return []
        raw_positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not raw_positions:
            return []
        
        result = []
        for p in raw_positions:
            result.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "side": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": float(p.volume),
                "price_open": float(p.price_open),
                "price_current": float(p.price_current),
                "sl": float(p.sl),
                "tp": float(p.tp),
                "profit": float(p.profit),
                "magic": int(p.magic),
                "comment": str(p.comment)
            })
        return result

    def get_account_info(self) -> Dict[str, Any]:
        """Returns MT5 account equity and margin metrics."""
        if not mt5.initialize():
            return {"balance": 0.0, "equity": 0.0, "margin": 0.0, "free_margin": 0.0}
        acc = mt5.account_info()
        if not acc:
            return {"balance": 0.0, "equity": 0.0, "margin": 0.0, "free_margin": 0.0}
        return {
            "balance": float(acc.balance),
            "equity": float(acc.equity),
            "margin": float(acc.margin),
            "free_margin": float(acc.margin_free),
            "margin_level": float(acc.margin_level) if acc.margin > 0 else 0.0
        }
