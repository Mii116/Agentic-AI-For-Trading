import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import MetaTrader5 as mt5

from app.db.session import SessionLocal
from app.models.trading import TradeJournal
from app.reports.performance import PerformanceReporter
from app.risk.var_calculator import ValueAtRiskCalculator
from app.agent.post_mortem import PostMortemEngine

logger = logging.getLogger(__name__)

class AnalyticsService:
    """
    Institutional Analytics & Business Intelligence Service:
    - Provides real-time dashboard KPIs: Equity, Balance, PnL, Drawdown.
    - Computes segregated metrics for Swing Trader (1001) and Scalp Trader (2002).
    - Checks and provides real-time status of Quantitative Circuit Breakers.
    - Calculates Value-at-Risk (VaR) on portfolio returns.
    """

    @classmethod
    def get_executive_overview(cls) -> Dict[str, Any]:
        equity = 0.0
        balance = 0.0
        margin_used = 0.0
        margin_free = 0.0
        margin_level = 0.0
        open_positions_count = 0

        if mt5.initialize():
            acc = mt5.account_info()
            if acc:
                equity = float(acc.equity)
                balance = float(acc.balance)
                margin_used = float(acc.margin)
                margin_free = float(acc.margin_free)
                margin_level = float(acc.margin_level) if acc.margin > 0 else 9999.0
            positions = mt5.positions_get() or []
            open_positions_count = len(positions)

        # Performance Stats
        overall_perf = PerformanceReporter.generate_performance_metrics()
        swing_perf = PerformanceReporter.generate_performance_metrics(magic_number=1001)
        scalp_perf = PerformanceReporter.generate_performance_metrics(magic_number=2002)

        # Scalper Circuit Breaker Check
        is_frozen, effective_scalp_risk, cb_msg = PostMortemEngine.check_scalper_circuit_breakers(equity)

        # Value at Risk calculation
        db = SessionLocal()
        trade_returns = []
        try:
            trades = db.query(TradeJournal).filter(TradeJournal.realized_pnl.isnot(None)).all()
            for t in trades:
                if equity > 0 and t.realized_pnl is not None:
                    trade_returns.append(t.realized_pnl / equity)
        finally:
            db.close()

        var_metrics = ValueAtRiskCalculator.calculate_var_metrics(trade_returns, equity)

        margin_utilization = round((margin_used / equity * 100.0), 2) if equity > 0 else 0.0
        shared_margin_safe = margin_utilization <= 20.0

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "account": {
                "balance": balance,
                "equity": equity,
                "margin_used": margin_used,
                "free_margin": margin_free,
                "margin_level_pct": margin_level,
                "margin_utilization_pct": margin_utilization,
                "shared_margin_safe": shared_margin_safe,
                "open_positions": open_positions_count
            },
            "performance": {
                "overall": overall_perf,
                "swing_1001": swing_perf,
                "scalp_2002": scalp_perf
            },
            "circuit_breakers": {
                "scalper_frozen": is_frozen,
                "scalper_status_message": cb_msg,
                "scalper_effective_risk_pct": effective_scalp_risk
            },
            "value_at_risk": var_metrics
        }
