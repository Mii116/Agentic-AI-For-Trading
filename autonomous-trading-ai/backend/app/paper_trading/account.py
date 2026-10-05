from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

@dataclass
class PaperPosition:
    ticket: int
    symbol: str
    side: str
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float = 0.0
    magic: int = 1001
    comment: str = ""
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class PaperAccount:
    """
    Virtual Paper Trading Ledger:
    - Maintains virtual balance, equity, margin, and positions without touching real broker funds.
    - Accurately tracks leverage (e.g. 1:2000), margin requirements, realized PnL, and unrealized floating PnL.
    """

    def __init__(self, initial_balance: float = 10000.0, leverage: float = 2000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.leverage = leverage
        self.positions: Dict[int, PaperPosition] = {}
        self.ticket_counter = 100000
        self.closed_trades_history: List[Dict[str, Any]] = []

    @property
    def equity(self) -> float:
        """Equity = Balance + Total Floating PnL of all open positions."""
        floating = sum(p.profit for p in self.positions.values())
        return round(self.balance + floating, 2)

    @property
    def margin_used(self) -> float:
        """Margin = Sum of (Volume * ContractSize * Price / Leverage)."""
        total_margin = 0.0
        contract_size = 100.0  # 100 oz per lot on Gold
        for p in self.positions.values():
            notional = p.volume * contract_size * p.price_open
            margin = notional / self.leverage
            total_margin += margin
        return round(total_margin, 2)

    @property
    def free_margin(self) -> float:
        return round(self.equity - self.margin_used, 2)

    @property
    def margin_level_pct(self) -> float:
        if self.margin_used <= 0:
            return 9999.0
        return round((self.equity / self.margin_used) * 100.0, 2)

    def next_ticket(self) -> int:
        self.ticket_counter += 1
        return self.ticket_counter

    def add_position(self, pos: PaperPosition):
        self.positions[pos.ticket] = pos

    def remove_position(self, ticket: int) -> Optional[PaperPosition]:
        return self.positions.pop(ticket, None)

    def record_closed_trade(self, pos: PaperPosition, exit_price: float, realized_pnl: float, exit_reason: str):
        self.balance = round(self.balance + realized_pnl, 2)
        self.closed_trades_history.append({
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "side": pos.side,
            "volume": pos.volume,
            "entry_price": pos.price_open,
            "exit_price": exit_price,
            "sl": pos.sl,
            "tp": pos.tp,
            "pnl": realized_pnl,
            "magic": pos.magic,
            "comment": pos.comment,
            "exit_reason": exit_reason,
            "opened_at": pos.opened_at.isoformat(),
            "closed_at": datetime.now(timezone.utc).isoformat()
        })
