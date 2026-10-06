import logging
import math
from typing import Tuple, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config.settings import settings

logger = logging.getLogger(__name__)

@dataclass
class TierConfig:
    tier_name: str
    tier_level: int
    max_risk_pct: float
    max_concurrent_positions: int
    min_equity: float
    max_equity: float

class AccountTier:
    TIER_1 = TierConfig("Tier 1 (< $250)", 1, 0.015, 1, 0.0, 250.0)
    TIER_2 = TierConfig("Tier 2 ($250 - $500)", 2, 0.020, 2, 250.0, 500.0)
    TIER_3 = TierConfig("Tier 3 (> $500)", 3, 0.020, 3, 500.0, float("inf"))

    @classmethod
    def get_tier(cls, equity: float) -> TierConfig:
        if equity < 250.0:
            return cls.TIER_1
        elif equity <= 500.0:
            return cls.TIER_2
        else:
            return cls.TIER_3

class CapitalManager:
    """
    Dynamic Capital & Account Scaling Gate:
    - Dynamic risk percentage based on equity tier ($50 to $1,000+)
    - Strict position count ceilings per tier
    - Mathematical lot sizing formula with exact broker point and tick normalization
    - Micro-Account Trap Guard: Rejection of trades where structural stop requires < min lot
    - 5% Daily Drawdown Circuit Breaker with 24-hour halt
    """

    def __init__(self):
        self.peak_equity_today: float = 0.0
        self.current_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.circuit_breaker_tripped: bool = False
        self.circuit_breaker_trip_time: Optional[datetime] = None
        self.circuit_breaker_cooldown_hours: float = 24.0

    def sync_equity(self, current_equity: float):
        """Update daily peak equity and reset day if UTC date changes."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.current_day:
            logger.info(f"New trading day detected ({today}). Resetting daily peak equity to ${current_equity:.2f}.")
            self.current_day = today
            self.peak_equity_today = current_equity

        if current_equity > self.peak_equity_today or self.peak_equity_today == 0.0:
            self.peak_equity_today = current_equity

    def check_circuit_breaker(self, current_equity: float) -> Tuple[bool, str]:
        """
        Circuit Breaker: If daily drawdown reaches 5.0%, halt all new trade generation for 24 hours.
        Returns: (is_tripped: bool, reason: str)
        """
        self.sync_equity(current_equity)

        now = datetime.now(timezone.utc)

        # Check if already tripped and if 24 hours have passed
        if self.circuit_breaker_tripped:
            if self.circuit_breaker_trip_time:
                elapsed = now - self.circuit_breaker_trip_time
                if elapsed < timedelta(hours=self.circuit_breaker_cooldown_hours):
                    remaining_hours = self.circuit_breaker_cooldown_hours - (elapsed.total_seconds() / 3600.0)
                    return True, f"Circuit Breaker ACTIVE: Daily drawdown exceeded 5.0%. Trading halted for next {remaining_hours:.1f} hours."
                else:
                    logger.info("Circuit breaker 24-hour cooldown period has elapsed. Resetting circuit breaker.")
                    self.circuit_breaker_tripped = False
                    self.circuit_breaker_trip_time = None
                    self.peak_equity_today = current_equity

        # Evaluate daily drawdown against 10.0% threshold (scaled for high-frequency pending order volume)
        if self.peak_equity_today > 0:
            drawdown_pct = (self.peak_equity_today - current_equity) / self.peak_equity_today
            if drawdown_pct >= 0.10:  # 10.0% threshold
                self.circuit_breaker_tripped = True
                self.circuit_breaker_trip_time = now
                msg = (f"CIRCUIT BREAKER TRIGGERED! Daily drawdown is {drawdown_pct * 100:.2f}% "
                       f"(Peak: ${self.peak_equity_today:.2f}, Current: ${current_equity:.2f}). "
                       f"Halting all new trades for 24 hours.")
                logger.error(msg)
                return True, msg

        return False, "Circuit Breaker Normal"

    def can_open_new_position(self, current_equity: float, current_open_positions_count: int) -> Tuple[bool, str, TierConfig]:
        """
        Validates if current open position count allows a new entry under the equity tier rules.
        """
        # First check circuit breaker
        is_tripped, cb_reason = self.check_circuit_breaker(current_equity)
        if is_tripped:
            return False, cb_reason, AccountTier.get_tier(current_equity)

        tier = AccountTier.get_tier(current_equity)

        # Cent Account Pyramiding Mode allows scaling into 2 to 5 concurrent positions
        max_positions = getattr(settings, "CENT_MAX_TRANCHES", 5) if getattr(settings, "CENT_ACCOUNT_MODE", False) else tier.max_concurrent_positions

        if current_open_positions_count >= max_positions:
            reason = (f"Account Tier Gate: Active positions ({current_open_positions_count}) "
                      f"reached maximum allowed ({max_positions}) for {tier.tier_name} (CentMode={getattr(settings, 'CENT_ACCOUNT_MODE', False)}).")
            logger.warning(reason)
            return False, reason, tier

        return True, f"Tier Check Passed: {tier.tier_name}", tier

    def calculate_lot_size(
        self,
        current_equity: float,
        entry_price: float,
        stop_loss: float,
        point: float = 0.01,
        tick_value_per_point: float = 1.0,
        volume_min: float = 0.01,
        volume_max: float = 100.0,
        volume_step: float = 0.01,
        risk_pct_override: Optional[float] = None
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Dynamic Lot Sizing Formula:
        MaxDollarRisk = CurrentEquity * RiskPct
        SLPoints = |EntryPrice - StopLoss| / Point
        LotSize = MaxDollarRisk / (SLPoints * TickValuePerPoint)
        
        Micro-Account Trap Guard:
        If account equity is $50 and the structural stop requires a lot size smaller than
        volume_min (e.g. 0.01) to maintain risk%, reject the trade proposal rather than
        forcing 0.01 and over-risking.
        
        Returns: (normalized_lot: Optional[float], error_message: Optional[str])
        """
        tier = AccountTier.get_tier(current_equity)
        risk_pct = risk_pct_override if risk_pct_override is not None else tier.max_risk_pct

        price_diff = abs(entry_price - stop_loss)
        if price_diff <= 0 or point <= 0:
            return None, "Invalid Stop Loss: distance to entry price is zero."

        # Cent Account Tranche Pyramiding Profile (200 - 1,000+ USC)
        if getattr(settings, "CENT_ACCOUNT_MODE", False):
            tranche_min = getattr(settings, "CENT_TRANCHE_MIN_LOT", 0.10)
            tranche_max = getattr(settings, "CENT_TRANCHE_MAX_LOT", 0.20)
            # Scale from 0.10 up to 0.20 as equity grows from 200 to 1,000 USC
            ratio = max(0.0, min(1.0, (current_equity - 200.0) / 800.0))
            cent_lot = round(tranche_min + (ratio * (tranche_max - tranche_min)), 2)
            cent_lot = max(volume_min, min(cent_lot, volume_max))
            logger.info(
                f"[CentPyramidEngine] Sized Tranche Lot: {cent_lot} for equity {current_equity:.1f} USC "
                f"(Range: {tranche_min} - {tranche_max})"
            )
            return cent_lot, None

        sl_points = price_diff / point
        max_dollar_risk = current_equity * risk_pct

        # Avoid division by zero
        denom = sl_points * tick_value_per_point
        if denom <= 0:
            return None, "Invalid stop loss or tick value parameters."

        raw_lot = max_dollar_risk / denom

        # Micro-Account Trap Guard Check:
        # If raw_lot is below the broker minimum volume (e.g., 0.01 lot)
        if raw_lot < volume_min:
            actual_risk_if_forced = volume_min * sl_points * tick_value_per_point
            actual_risk_pct_if_forced = (actual_risk_if_forced / current_equity) * 100
            err_msg = (
                f"MICRO-ACCOUNT TRAP GUARD ACTIVATED: Structural SL ({sl_points:.1f} pts) "
                f"with {risk_pct*100:.1f}% risk budget (${max_dollar_risk:.2f} on equity ${current_equity:.2f}) "
                f"requires {raw_lot:.4f} lots, which is below broker min lot {volume_min}. "
                f"Forcing {volume_min} lot would risk ${actual_risk_if_forced:.2f} ({actual_risk_pct_if_forced:.1f}% of equity). "
                f"Trade proposal REJECTED to prevent account destruction."
            )
            logger.warning(err_msg)
            return None, err_msg

        # Round down strictly to volume_step to prevent risking even a penny over risk budget
        steps = math.floor(raw_lot / volume_step)
        normalized_lot = steps * volume_step
        normalized_lot = round(normalized_lot, 2)

        # Ensure lot stays within broker bounds
        if normalized_lot < volume_min:
            return None, f"Calculated normalized lot {normalized_lot} is below broker min lot {volume_min}."
        if normalized_lot > volume_max:
            normalized_lot = volume_max

        logger.info(
            f"Dynamic Lot Sizing: Equity=${current_equity:.2f} ({tier.tier_name}), "
            f"Risk={risk_pct*100:.1f}% (${max_dollar_risk:.2f}), SL Distance={price_diff:.2f} ({sl_points:.0f} pts) "
            f"-> Sized Lot={normalized_lot}"
        )
        return normalized_lot, None
