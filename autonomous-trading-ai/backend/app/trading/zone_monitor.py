import time
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import MetaTrader5 as mt5

from app.agents.trade_proposal import TradeProposal
from app.risk.stop_policy import scalp_stop_distance, enforce_min_stop, MIN_STOP_DISTANCE

logger = logging.getLogger(__name__)


@dataclass
class ArmedZone:
    zone_id: str
    proposal: TradeProposal
    lot_size: float
    direction: str                # "BUY" or "SELL"
    zone_low: float               # Lower boundary of FVG or sweep wick
    zone_high: float              # Upper boundary of FVG or sweep wick
    invalidation_price: float     # Price beyond which the zone is broken (falling knife)
    structural_sl: float          # Initial structural SL anchor
    atr_m5: float
    target_rr: float
    magic_number: int
    armed_at: float
    ttl_seconds: float
    notes: str = ""
    entered_zone: bool = False
    touch_extreme: float = 0.0    # Lowest bid seen in zone (BUY), or highest ask seen (SELL)
    recent_ticks: List[float] = field(default_factory=list)


class ZoneRetestMonitor:
    """
    Dynamic Zone-Retest Confirmation Engine:
    - Replaces passive 50% limit orders to eliminate ADVERSE SELECTION.
    - Arms an active zone (FVG or session sweep wick) without placing a passive order.
    - Monitors incoming live ticks:
      1. KNIFE FILTER: If price blows straight through invalidation_price, disarms
         immediately with zero loss (prevents catching falling knives).
      2. ZONE MITIGATION: Waits until price enters [zone_low, zone_high].
      3. DISPLACEMENT CONFIRMATION TICK: Once inside or rebounding, requires
         momentum in the trade direction (higher than prior 3 ticks for BUY, lower
         for SELL, plus at least $0.15 bounce from touch extreme).
      4. MARKET DISPATCH: Fires a fast market execution with a volatility-adjusted
         stop loss (>= 1.5 x ATR(M5), floor $1.50-$2.00).
    """

    def __init__(self, execution_engine=None):
        self.execution_engine = execution_engine
        self.armed_zones: Dict[str, ArmedZone] = {}

    def arm_zone(self, proposal: TradeProposal, lot_size: float, notes: str = "") -> bool:
        """Arms a validated zone proposal for dynamic retest confirmation."""
        zl = proposal.zone_low if proposal.zone_low is not None else proposal.entry_price
        zh = proposal.zone_high if proposal.zone_high is not None else proposal.entry_price
        inv = proposal.invalidation_price if proposal.invalidation_price is not None else proposal.structural_sl

        zone = ArmedZone(
            zone_id=proposal.proposal_id,
            proposal=proposal,
            lot_size=lot_size,
            direction=proposal.direction.upper(),
            zone_low=round(float(zl), 2),
            zone_high=round(float(zh), 2),
            invalidation_price=round(float(inv), 2),
            structural_sl=round(float(proposal.structural_sl), 2),
            atr_m5=float(getattr(proposal, "atr_m5", 2.0) or 2.0),
            target_rr=float(getattr(proposal, "target_rr", 2.0) or 2.0),
            magic_number=proposal.magic_number,
            armed_at=time.time(),
            ttl_seconds=float(getattr(proposal, "ttl_minutes", 20) * 60.0),
            notes=notes
        )

        self.armed_zones[zone.zone_id] = zone
        logger.info(
            f"[ZoneRetestMonitor] ARMED ZONE #{zone.zone_id}: {zone.direction} [{zone.zone_low} - {zone.zone_high}] | "
            f"Invalidation={zone.invalidation_price} | SL Anchor={zone.structural_sl} | Lot={zone.lot_size} | "
            f"TTL={zone.ttl_seconds / 60:.0f}m (Awaiting mitigation & confirmation tick)"
        )
        return True

    def disarm_zone(self, zone_id: str, reason: str = ""):
        """Disarms and removes an armed zone."""
        if zone_id in self.armed_zones:
            zone = self.armed_zones.pop(zone_id)
            logger.info(f"[ZoneRetestMonitor] Disarmed Zone #{zone.zone_id} ({zone.direction}): {reason}")

    def tick_check(self, symbol: str = "XAUUSD") -> List[Dict[str, Any]]:
        """
        High-frequency check executed on every tick/heartbeat:
        - Evaluates TTL expirations.
        - Evaluates falling knife invalidations.
        - Detects zone mitigations & displacement confirmation ticks.
        - Executes market orders immediately when confirmed.
        """
        if not self.armed_zones:
            return []

        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.bid <= 0 or tick.ask <= 0:
            return []

        now = time.time()
        executed_events = []
        to_remove = []

        for zone_id, zone in list(self.armed_zones.items()):
            # 1. Check TTL Expiration
            age = now - zone.armed_at
            if age >= zone.ttl_seconds:
                logger.info(
                    f"[ZoneRetestMonitor] TTL Expired for #{zone_id} ({age:.0f}s >= {zone.ttl_seconds:.0f}s). Disarming."
                )
                to_remove.append(zone_id)
                continue

            current_bid = float(tick.bid)
            current_ask = float(tick.ask)

            # 2. Check Falling Knife Invalidation
            # If price blows straight through the invalidation price before/during retest
            if zone.direction == "BUY":
                if current_bid <= zone.invalidation_price:
                    logger.warning(
                        f"[ZoneRetestMonitor] KNIFE FILTER TRIGGERED for #{zone_id} (BUY): "
                        f"Price dropped to {current_bid:.2f} <= invalidation {zone.invalidation_price:.2f}. "
                        f"Disarming without taking a loss!"
                    )
                    to_remove.append(zone_id)
                    continue

                # 3. Check Zone Mitigation (BUY: price dips into [zone_low, zone_high])
                if current_bid <= zone.zone_high:
                    if not zone.entered_zone:
                        zone.entered_zone = True
                        zone.touch_extreme = current_bid
                        logger.info(
                            f"[ZoneRetestMonitor] Zone Mitigated! #{zone_id} (BUY) entered zone at {current_bid:.2f}. "
                            f"Tracking confirmation tick..."
                        )
                    else:
                        zone.touch_extreme = min(zone.touch_extreme, current_bid)

                    # Track recent price stream for displacement
                    zone.recent_ticks.append(current_ask)
                    if len(zone.recent_ticks) > 6:
                        zone.recent_ticks.pop(0)

                    # 4. Displacement Confirmation Trigger
                    # Confirmation: price is rebounding up, printing higher than prior 3 ticks and >= $0.15 bounce
                    bounce_dist = current_ask - zone.touch_extreme
                    has_tick_momentum = False
                    if len(zone.recent_ticks) >= 4:
                        # Current tick is higher than the previous 3 ticks
                        t1, t2, t3 = zone.recent_ticks[-4], zone.recent_ticks[-3], zone.recent_ticks[-2]
                        if current_ask > max(t1, t2, t3):
                            has_tick_momentum = True

                    if has_tick_momentum and bounce_dist >= 0.15:
                        logger.info(
                            f"[ZoneRetestMonitor] CONFIRMATION CONFIRMED for #{zone_id} (BUY)! "
                            f"Ask {current_ask:.2f} bounced +${bounce_dist:.2f} from low {zone.touch_extreme:.2f} "
                            f"with tick momentum ({zone.recent_ticks[-4:]}). Firing market execution!"
                        )
                        success = self._fire_confirmed_market_order(zone, current_ask, symbol)
                        to_remove.append(zone_id)
                        if success:
                            executed_events.append({"zone_id": zone_id, "side": "BUY", "price": current_ask})

            elif zone.direction == "SELL":
                if current_ask >= zone.invalidation_price:
                    logger.warning(
                        f"[ZoneRetestMonitor] KNIFE FILTER TRIGGERED for #{zone_id} (SELL): "
                        f"Price spiked to {current_ask:.2f} >= invalidation {zone.invalidation_price:.2f}. "
                        f"Disarming without taking a loss!"
                    )
                    to_remove.append(zone_id)
                    continue

                # 3. Check Zone Mitigation (SELL: price rallies into [zone_low, zone_high])
                if current_ask >= zone.zone_low:
                    if not zone.entered_zone:
                        zone.entered_zone = True
                        zone.touch_extreme = current_ask
                        logger.info(
                            f"[ZoneRetestMonitor] Zone Mitigated! #{zone_id} (SELL) entered zone at {current_ask:.2f}. "
                            f"Tracking confirmation tick..."
                        )
                    else:
                        zone.touch_extreme = max(zone.touch_extreme, current_ask)

                    # Track recent price stream for displacement
                    zone.recent_ticks.append(current_bid)
                    if len(zone.recent_ticks) > 6:
                        zone.recent_ticks.pop(0)

                    # 4. Displacement Confirmation Trigger
                    # Confirmation: price is rejecting down, printing lower than prior 3 ticks and >= $0.15 drop
                    drop_dist = zone.touch_extreme - current_bid
                    has_tick_momentum = False
                    if len(zone.recent_ticks) >= 4:
                        t1, t2, t3 = zone.recent_ticks[-4], zone.recent_ticks[-3], zone.recent_ticks[-2]
                        if current_bid < min(t1, t2, t3):
                            has_tick_momentum = True

                    if has_tick_momentum and drop_dist >= 0.15:
                        logger.info(
                            f"[ZoneRetestMonitor] CONFIRMATION CONFIRMED for #{zone_id} (SELL)! "
                            f"Bid {current_bid:.2f} dropped -${drop_dist:.2f} from high {zone.touch_extreme:.2f} "
                            f"with tick momentum ({zone.recent_ticks[-4:]}). Firing market execution!"
                        )
                        success = self._fire_confirmed_market_order(zone, current_bid, symbol)
                        to_remove.append(zone_id)
                        if success:
                            executed_events.append({"zone_id": zone_id, "side": "SELL", "price": current_bid})

        for zid in to_remove:
            self.armed_zones.pop(zid, None)

        return executed_events

    def _fire_confirmed_market_order(self, zone: ArmedZone, fill_price: float, symbol: str) -> bool:
        """Executes a confirmed market order with volatility-adjusted SL/TP."""
        if not self.execution_engine:
            logger.error("[ZoneRetestMonitor] No execution engine configured to dispatch order.")
            return False

        # Calculate volatility-adjusted stop loss based on actual fill price
        if zone.direction == "BUY":
            struct_dist = max(0.50, fill_price - zone.structural_sl)
            sl_dist = scalp_stop_distance(struct_dist, zone.atr_m5)
            sl = round(fill_price - sl_dist, 2)
            sl, _ = enforce_min_stop("BUY", fill_price, sl, min_dist=MIN_STOP_DISTANCE)
            tp = round(fill_price + (zone.target_rr * sl_dist), 2)
        else:
            struct_dist = max(0.50, zone.structural_sl - fill_price)
            sl_dist = scalp_stop_distance(struct_dist, zone.atr_m5)
            sl = round(fill_price + sl_dist, 2)
            sl, _ = enforce_min_stop("SELL", fill_price, sl, min_dist=MIN_STOP_DISTANCE)
            tp = round(fill_price - (zone.target_rr * sl_dist), 2)

        comment = f"Retest-{zone.zone_id[:8]}"
        zone.proposal.entry_price = fill_price
        zone.proposal.structural_sl = sl
        zone.proposal.suggested_tp = tp

        logger.info(
            f"[ZoneRetestMonitor] Dispatching MT5 Market Order: {zone.direction} {zone.lot_size} lots at {fill_price:.2f} | "
            f"SL={sl:.2f} (Dist: ${abs(fill_price - sl):.2f}) | TP={tp:.2f} (1:{zone.target_rr:.1f} R:R)"
        )

        return self.execution_engine.execute_custom_order(
            symbol=symbol,
            side=zone.direction,
            volume=zone.lot_size,
            stop_loss=sl,
            take_profit=tp,
            comment=comment,
            proposal=zone.proposal
        )
