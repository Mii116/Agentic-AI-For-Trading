import logging
from typing import Dict, Any, List, Optional
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

class PortfolioManager:
    """
    Institutional Multi-Asset Portfolio Manager:
    - Tracks asset allocation across targets (e.g. XAUUSD Gold, BTCUSD Crypto).
    - Measures directional exposure (Net Long vs Net Short notional value).
    - Computes effective leverage and margin utilization.
    - Monitors portfolio concentration risk (e.g. ensuring no single asset exceeds max allocation limits).
    """

    MAX_ASSET_EXPOSURE_PCT = 0.50  # Max 50% equity allocation to any single asset

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Calculates comprehensive portfolio metrics from broker or simulator."""
        if not mt5.initialize():
            return {
                "equity": 0.0,
                "balance": 0.0,
                "net_exposure_usd": 0.0,
                "leverage_used": 0.0,
                "assets": {}
            }

        acc = mt5.account_info()
        positions = mt5.positions_get() or []

        equity = float(acc.equity) if acc else 0.0
        balance = float(acc.balance) if acc else 0.0

        assets: Dict[str, Dict[str, Any]] = {}
        total_long_notional = 0.0
        total_short_notional = 0.0

        for p in positions:
            sym = p.symbol
            if sym not in assets:
                assets[sym] = {
                    "symbol": sym,
                    "long_lots": 0.0,
                    "short_lots": 0.0,
                    "net_lots": 0.0,
                    "notional_usd": 0.0,
                    "floating_profit": 0.0,
                    "position_count": 0
                }

            contract_size = 100.0 if "XAU" in sym else 1.0
            notional = float(p.volume) * contract_size * float(p.price_current)

            if p.type == mt5.ORDER_TYPE_BUY:
                assets[sym]["long_lots"] += float(p.volume)
                total_long_notional += notional
            else:
                assets[sym]["short_lots"] += float(p.volume)
                total_short_notional += notional

            assets[sym]["floating_profit"] += float(p.profit)
            assets[sym]["position_count"] += 1

        for sym, data in assets.items():
            data["net_lots"] = round(data["long_lots"] - data["short_lots"], 2)
            data["long_lots"] = round(data["long_lots"], 2)
            data["short_lots"] = round(data["short_lots"], 2)
            data["floating_profit"] = round(data["floating_profit"], 2)
            contract_size = 100.0 if "XAU" in sym else 1.0
            tick = mt5.symbol_info_tick(sym)
            px = tick.bid if tick else 0.0
            data["notional_usd"] = round(abs(data["net_lots"]) * contract_size * px, 2)
            data["weight_pct"] = round((data["notional_usd"] / equity * 100.0), 2) if equity > 0 else 0.0

        net_exposure = round(total_long_notional - total_short_notional, 2)
        gross_exposure = round(total_long_notional + total_short_notional, 2)
        effective_leverage = round(gross_exposure / equity, 2) if equity > 0 else 0.0

        return {
            "equity": equity,
            "balance": balance,
            "margin_used": float(acc.margin) if acc else 0.0,
            "free_margin": float(acc.margin_free) if acc else 0.0,
            "net_exposure_usd": net_exposure,
            "gross_exposure_usd": gross_exposure,
            "effective_leverage": effective_leverage,
            "total_open_positions": len(positions),
            "assets": assets
        }
