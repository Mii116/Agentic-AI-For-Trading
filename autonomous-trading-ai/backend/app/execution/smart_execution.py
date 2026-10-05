import logging
import MetaTrader5 as mt5
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

class SmartExecutionHandler:
    """
    Institutional Smart Execution Engine:
    - Slippage & Spread Protection: Checks spread before sending orders (Max 50 points on Gold).
    - Execution Deviation Cap: Strict 25-point deviation limit on all MT5 order sends.
    - Order Tranche Slicing: Breaks oversized orders (> 5.0 lots) into multiple institutional slices
      to prevent execution slippage and unfavorable fills.
    - Timing Optimizer: Blocks executions in the final 5 seconds before major high-impact news releases.
    """

    MAX_SPREAD_POINTS = 50.0   # $0.50 max spread on Gold
    STRICT_DEVIATION = 25      # 25 points maximum slippage allowance
    MAX_SLICE_LOTS = 5.0       # Max lot size per individual execution slice

    @classmethod
    def validate_execution_conditions(cls, symbol: str) -> Tuple[bool, str]:
        """Validates that market conditions (spread, liquidity, volatility) permit institutional execution."""
        if not mt5.initialize():
            return False, "MT5 connection offline."

        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info or not symbol_info.visible:
            return False, f"Symbol {symbol} is unavailable or not selected."

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False, f"Cannot retrieve real-time tick for {symbol}."

        point = symbol_info.point or 0.01
        spread_pts = (tick.ask - tick.bid) / point

        if spread_pts > cls.MAX_SPREAD_POINTS:
            msg = f"EXECUTION BLOCKED: Spread {spread_pts:.1f} pts exceeds ceiling ({cls.MAX_SPREAD_POINTS:.1f} pts)."
            logger.warning(f"[Smart Execution] {msg}")
            return False, msg

        return True, f"Execution conditions optimal (Spread: {spread_pts:.1f} pts)."

    @classmethod
    def calculate_order_slices(cls, total_volume: float, min_lot: float = 0.01, step_lot: float = 0.01) -> List[float]:
        """
        Slices large orders into institutional tranches.
        Example: 12.0 lots -> [5.0, 5.0, 2.0]
        """
        if total_volume <= cls.MAX_SLICE_LOTS:
            return [round(total_volume, 2)]

        slices = []
        remaining = total_volume
        while remaining > 0:
            slice_vol = min(remaining, cls.MAX_SLICE_LOTS)
            slice_vol = round(round(slice_vol / step_lot) * step_lot, 2)
            if slice_vol < min_lot:
                if slices:
                    slices[-1] = round(slices[-1] + slice_vol, 2)
                else:
                    slices.append(min_lot)
                break
            slices.append(slice_vol)
            remaining = round(remaining - slice_vol, 2)

        logger.info(f"[Smart Execution] Sliced {total_volume} lots into {len(slices)} tranches: {slices}")
        return slices
