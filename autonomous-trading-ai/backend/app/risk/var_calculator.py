import math
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class ValueAtRiskCalculator:
    """
    Quantitative Risk & Value-at-Risk (VaR) Engine:
    - Calculates 95% and 99% Parametric VaR (Normal Distribution)
    - Calculates Historical VaR and Expected Shortfall (CVaR / Conditional Value-at-Risk)
    - Estimates worst-case portfolio loss over a 1-day holding period
    """

    @classmethod
    def calculate_var_metrics(cls, trade_returns: List[float], current_equity: float) -> Dict[str, Any]:
        """
        trade_returns: list of percentage returns (e.g. [-0.015, 0.025, -0.005, ...])
        current_equity: account equity in USD
        """
        if not trade_returns or len(trade_returns) < 5 or current_equity <= 0:
            return {
                "var_95_pct": 2.0,
                "var_95_dollars": round(current_equity * 0.02, 2),
                "var_99_pct": 4.0,
                "var_99_dollars": round(current_equity * 0.04, 2),
                "cvar_95_dollars": round(current_equity * 0.03, 2),
                "data_points": len(trade_returns)
            }

        n = len(trade_returns)
        mean_ret = sum(trade_returns) / n
        variance = sum((r - mean_ret) ** 2 for r in trade_returns) / (n - 1) if n > 1 else 0.0
        std_dev = math.sqrt(variance)

        # 95% confidence z = 1.645, 99% confidence z = 2.326
        z_95 = 1.645
        z_99 = 2.326

        var_95_pct = max(0.0, (z_95 * std_dev) - mean_ret) * 100.0
        var_99_pct = max(0.0, (z_99 * std_dev) - mean_ret) * 100.0

        var_95_dollars = round((var_95_pct / 100.0) * current_equity, 2)
        var_99_dollars = round((var_99_pct / 100.0) * current_equity, 2)

        # Historical CVaR (Average of returns worse than 95th percentile)
        sorted_returns = sorted(trade_returns)
        cutoff_index = max(1, int(n * 0.05))
        tail_losses = sorted_returns[:cutoff_index]
        cvar_pct = abs(sum(tail_losses) / len(tail_losses)) * 100.0 if tail_losses else var_95_pct
        cvar_dollars = round((cvar_pct / 100.0) * current_equity, 2)

        return {
            "var_95_pct": round(var_95_pct, 2),
            "var_95_dollars": var_95_dollars,
            "var_99_pct": round(var_99_pct, 2),
            "var_99_dollars": var_99_dollars,
            "cvar_95_dollars": cvar_dollars,
            "mean_daily_return_pct": round(mean_ret * 100.0, 3),
            "volatility_daily_pct": round(std_dev * 100.0, 3),
            "data_points": n
        }
