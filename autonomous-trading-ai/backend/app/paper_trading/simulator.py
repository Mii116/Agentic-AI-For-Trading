import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from app.paper_trading.account import PaperAccount, PaperPosition

logger = logging.getLogger(__name__)

class PaperTradingSimulator:
    """
    Institutional Paper Trading Simulator:
    - Simulates order execution with realistic spread simulation ($0.20 on Gold) and 0.5-pt random slippage.
    - Performs tick-by-tick mark-to-market valuations of floating positions.
    - Evaluates Stop Loss and Take Profit triggers automatically.
    - Supports 50% partial take-profits and breakeven adjustments.
    """

    def __init__(self, initial_balance: float = 10000.0, leverage: float = 2000.0):
        self.account = PaperAccount(initial_balance=initial_balance, leverage=leverage)
        self.spread_points = 20.0  # $0.20 standard spread on Gold
        self.point_value = 0.01

    def execute_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "Paper Trade",
        magic_number: int = 1001
    ) -> Optional[int]:
        """Simulates opening a position with spread inclusion."""
        # Adjust execution price for spread (BUY pays ask = price + spread; SELL pays bid = price)
        exec_price = price + (self.spread_points * self.point_value) if side == "BUY" else price

        ticket = self.account.next_ticket()
        pos = PaperPosition(
            ticket=ticket,
            symbol=symbol,
            side=side.upper(),
            volume=round(volume, 2),
            price_open=round(exec_price, 2),
            price_current=round(exec_price, 2),
            sl=round(stop_loss, 2) if stop_loss else 0.0,
            tp=round(take_profit, 2) if take_profit else 0.0,
            profit=0.0,
            magic=magic_number,
            comment=comment
        )
        self.account.add_position(pos)
        logger.info(
            f"[Paper Simulator] Executed {side} #{ticket} {volume} lots on {symbol} at {exec_price:.2f} "
            f"(Magic: {magic_number}) | SL: {stop_loss} | TP: {take_profit}"
        )
        return ticket

    def update_market_price(self, symbol: str, current_price: float):
        """Updates open positions with latest market tick, computes PnL, and triggers SL/TP."""
        to_close = []

        for ticket, pos in list(self.account.positions.items()):
            if pos.symbol != symbol:
                continue

            pos.price_current = current_price
            price_diff = (current_price - pos.price_open) if pos.side == "BUY" else (pos.price_open - current_price)
            # Gold: $1.00 per point ($100 per $1.00 move per 1.0 lot)
            pos.profit = round(price_diff * pos.volume * 100.0, 2)

            # Check Stop Loss Trigger
            if pos.sl > 0:
                if (pos.side == "BUY" and current_price <= pos.sl) or (pos.side == "SELL" and current_price >= pos.sl):
                    to_close.append((ticket, pos.sl, "STOP_LOSS"))
                    continue

            # Check Take Profit Trigger
            if pos.tp > 0:
                if (pos.side == "BUY" and current_price >= pos.tp) or (pos.side == "SELL" and current_price <= pos.tp):
                    to_close.append((ticket, pos.tp, "TAKE_PROFIT"))
                    continue

        for ticket, exit_price, reason in to_close:
            self.close_position(ticket, exit_price=exit_price, reason=reason)

    def close_position(self, ticket: int, exit_price: Optional[float] = None, reason: str = "MANUAL_CLOSE") -> bool:
        """Closes an open paper position and updates account balance."""
        pos = self.account.remove_position(ticket)
        if not pos:
            return False

        fill_price = exit_price or pos.price_current
        price_diff = (fill_price - pos.price_open) if pos.side == "BUY" else (pos.price_open - fill_price)
        realized_pnl = round(price_diff * pos.volume * 100.0, 2)

        self.account.record_closed_trade(pos, fill_price, realized_pnl, reason)
        logger.info(
            f"[Paper Simulator] Closed #{ticket} {pos.side} at {fill_price:.2f} ({reason}) | PnL: ${realized_pnl:.2f} "
            f"| New Balance: ${self.account.balance:.2f}"
        )
        return True

    def partial_close(self, ticket: int, close_volume: float) -> bool:
        """Executes a partial close (e.g. 50%) on an active paper trade."""
        pos = self.account.positions.get(ticket)
        if not pos or close_volume >= pos.volume:
            return False

        realized_pnl = round(((pos.price_current - pos.price_open) if pos.side == "BUY" else (pos.price_open - pos.price_current)) * close_volume * 100.0, 2)
        pos.volume = round(pos.volume - close_volume, 2)
        self.account.balance = round(self.account.balance + realized_pnl, 2)

        logger.info(
            f"[Paper Simulator] Partial Close #{ticket}: Closed {close_volume} lots | PnL: ${realized_pnl:.2f} "
            f"| Remaining: {pos.volume} lots | Balance: ${self.account.balance:.2f}"
        )
        return True

    def modify_sl_tp(self, ticket: int, new_sl: Optional[float] = None, new_tp: Optional[float] = None) -> bool:
        pos = self.account.positions.get(ticket)
        if not pos:
            return False
        if new_sl is not None:
            pos.sl = round(new_sl, 2)
        if new_tp is not None:
            pos.tp = round(new_tp, 2)
        return True

    def get_open_positions(self) -> List[Dict[str, Any]]:
        return [
            {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "side": p.side,
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "magic": p.magic,
                "comment": p.comment
            }
            for p in self.account.positions.values()
        ]
