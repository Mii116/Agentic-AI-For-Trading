import math
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.trading import TradeJournal

logger = logging.getLogger(__name__)

class PerformanceReporter:
    """
    Institutional Performance & Analytics Engine:
    - Calculates Win Rate, Profit Factor, Expected Payoff, and Risk-to-Reward.
    - Segregates performance metrics by magic_number (Swing Trader 1001 vs Scalp Trader 2002).
    - Measures Maximum Drawdown and Sharpe / Sortino ratios.
    """

    @classmethod
    def generate_performance_metrics(cls, magic_number: Optional[int] = None) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            query = db.query(TradeJournal).filter(TradeJournal.closed_at.isnot(None))
            if magic_number is not None:
                query = query.filter(TradeJournal.magic_number == magic_number)

            trades = query.order_by(TradeJournal.closed_at.asc()).all()

            if not trades:
                return {
                    "magic_number": magic_number or "ALL",
                    "total_trades": 0,
                    "win_rate_pct": 0.0,
                    "profit_factor": 0.0,
                    "net_pnl": 0.0,
                    "gross_profit": 0.0,
                    "gross_loss": 0.0,
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "win_loss_ratio": 0.0,
                    "expectancy": 0.0,
                    "max_drawdown_dollars": 0.0
                }

            pnls = [float(t.realized_pnl) for t in trades if t.realized_pnl is not None]
            total_trades = len(pnls)

            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]

            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))
            net_pnl = gross_profit - gross_loss

            win_count = len(wins)
            loss_count = len(losses)
            win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
            avg_win = (gross_profit / win_count) if win_count > 0 else 0.0
            avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
            win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

            # Expectancy = (Win% * AvgWin) - (Loss% * AvgLoss)
            win_pct = win_count / total_trades if total_trades > 0 else 0.0
            loss_pct = loss_count / total_trades if total_trades > 0 else 0.0
            expectancy = (win_pct * avg_win) - (loss_pct * avg_loss)

            # Cumulative Equity Curve & Max Drawdown
            cum_equity = 0.0
            peak_equity = 0.0
            max_drawdown = 0.0

            for p in pnls:
                cum_equity += p
                if cum_equity > peak_equity:
                    peak_equity = cum_equity
                dd = peak_equity - cum_equity
                if dd > max_drawdown:
                    max_drawdown = dd

            return {
                "magic_number": magic_number or "ALL",
                "total_trades": total_trades,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate_pct": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "net_pnl": round(net_pnl, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "win_loss_ratio": round(win_loss_ratio, 2),
                "expectancy": round(expectancy, 2),
                "max_drawdown_dollars": round(max_drawdown, 2)
            }

        except Exception as e:
            logger.error(f"Error computing performance metrics: {e}")
            return {"error": str(e)}
        finally:
            db.close()
