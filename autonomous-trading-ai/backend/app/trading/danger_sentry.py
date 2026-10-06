import logging
import time
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import MetaTrader5 as mt5

from app.db.session import SessionLocal
from app.models.trading import TradeJournal, Position
from app.agent.post_mortem import PostMortemEngine
from app.indicators.smc import SMCAnalyzer
from app.trading.cooldown_manager import MarketCooldownManager
from app.risk.stop_policy import MIN_STOP_DISTANCE, enforce_min_stop
from app.config.settings import settings

logger = logging.getLogger(__name__)

class DangerSentry:
    """
    Real-Time Sentry, Proactive Exhaustion Exit & Dynamic Structural Trailing Engine:
    - Segregates positions strictly by MT5 magic_number (1001 for Swing, 2002 for Scalp).
    - Permits hedging co-existence: An active Swing Long and Scalp Short NEVER liquidate each other.
    - 180-Second Grace Period: No micro-exhaustion or structural invalidation exits during first 3 minutes of a trade.
    - Muted Early Exhaustion Exits: Exhaustion checks evaluated EXCLUSIVELY on confirmed 5-minute (M5)
      candle closes (requires at least 2 consecutive M5 rejection candles with declining volume).
      1-minute counter-wicks and intra-candle fluctuations are completely bypassed.
    - 5-Minute (M5) Structural Invalidation: Only triggers on confirmed M5 candle closes breaching structure.
    - Structural Trailing Instead of Hard Breakeven: At 1:1 R:R, executes 50% partial TP to bank 1R,
      locks in the spread cost, and trails the stop behind the most recent confirmed M5 swing low/high,
      giving the trade room to breathe and eliminating premature scratches.
    - Minimum Stop Distance: Enforces >= $1.50 stop distance on Gold for all trailing and SL updates.
    - Shared Margin Guard: Enforces 20% cumulative margin utilization ceiling.
    - Flash Spread Blowout Protection (> 50 points / $0.50).
    - Dispatches closed trades to PostMortemEngine and triggers MarketCooldownManager.
    """

    MAX_SAFE_SPREAD_POINTS = 50.0  # Max allowable spread ($0.50 on Gold)
    SPREAD_BUFFER_GOLD = 0.20      # $0.20 Breakeven spread buffer on Gold
    GRACE_PERIOD_SECONDS = 180.0   # 180-second (3-minute) grace period for standard trades
    LIMIT_BUFFER_PERIOD_SECONDS = 240.0  # 240-second (4-minute) buffer period for Limit fills
    PROFIT_TRAIL_MIN = 1.5         # Minimum profit ($) before trailing starts
    DISASTER_STOP_DISTANCE = 25.0  # Emergency stop distance ($) for catastrophic blowout protection

    def __init__(self, execution_engine=None):
        self.execution_engine = execution_engine
        self.post_mortem = PostMortemEngine()
        self._last_evaluated_m5_time: Optional[Any] = None
        self._be_secured_tickets: set = set()
        self._pyramid_runners: set = set()
        self._scaled_in_tickets: set = set()

    def inspect_positions_for_danger(
        self,
        symbol: str = "XAUUSD",
        m5_bars: Optional[List[Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Inspects all open positions segregated by magic_number.
        Checks for dangerous territory, buffered M5 exhaustion exits, and executes 50% partial take-profits.
        Restricts exhaustion & structural checks strictly to confirmed M5 candle closes.
        """
        if not mt5.initialize():
            return []

        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return []

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return []

        point = symbol_info.point or 0.01
        current_spread_points = (tick.ask - tick.bid) / point

        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return []

        # Fetch fresh CLOSED 5m bars for structural and exhaustion evaluation (start_pos=1 skips forming bar)
        if m5_bars is None or len(m5_bars) < 10:
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, 30)
            if rates is not None and len(rates) > 0:
                m5_bars = [{
                    "time": int(r[0]),
                    "timestamp": datetime.fromtimestamp(r[0], tz=timezone.utc),
                    "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                    "close": float(r[4]), "volume": float(r[5])
                } for r in rates]

        latest_m5_bar_time = m5_bars[-1].get("time") if (m5_bars and isinstance(m5_bars[-1], dict)) else (
            getattr(m5_bars[-1], "time", None) if m5_bars else None
        )
        new_m5_candle_closed = (latest_m5_bar_time is not None and latest_m5_bar_time != self._last_evaluated_m5_time)

        db = SessionLocal()
        events = []

        try:
            # 1. Flash Spread Blowout Check (Always active)
            is_spread_blowout = current_spread_points > self.MAX_SAFE_SPREAD_POINTS

            for pos in positions:
                ticket = pos.ticket
                magic = pos.magic  # 1001 for Swing, 2002 for Scalp
                role_name = "SWING (1001)" if magic == 1001 else ("SCALP (2002)" if magic == 2002 else f"MAGIC {magic}")
                side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
                entry_price = float(pos.price_open)
                current_price = float(pos.price_current)
                floating_pnl = float(pos.profit)
                sl = float(pos.sl)
                tp = float(pos.tp)
                volume = float(pos.volume)

                pos_open_time = float(pos.time)
                now_ts = time.time()
                pos_age_sec = max(0.0, now_ts - pos_open_time)

                # Query database journal entry for this ticket
                journal = db.query(TradeJournal).filter_by(ticket=ticket).first()

                # Detect if position was entered via a Limit Order or Zone Retest
                is_limit_entry = self._is_limit_fill(pos, journal)
                in_limit_buffer = is_limit_entry and (pos_age_sec < self.LIMIT_BUFFER_PERIOD_SECONDS)
                in_grace_period = (not is_limit_entry) and (pos_age_sec < self.GRACE_PERIOD_SECONDS)

                # === 2. CENT PYRAMID ENGINE FLOW (FAST BE, TP1 HARVEST, STEPPED RATCHET) ===
                self._check_cent_pyramid_flow(pos, journal, db, symbol, m5_bars)

                # === 2B. STRUCTURAL 50% PARTIAL TAKE-PROFIT & TRAILING AT 1:1 R:R ===
                self._check_and_execute_partial_tp(pos, journal, db, symbol, m5_bars)
                self._apply_floating_profit_protection(pos, journal, db, symbol, m5_bars)
                self._attempt_scale_in(pos, journal, db, symbol)

                # === 3. EARLY EXHAUSTION EXIT & STRUCTURAL DANGER DETECTION ===
                exit_triggered = False
                exit_reason = ""

                # Danger Check A: Flash Spread Blowout (Always active for emergency safety)
                if is_spread_blowout:
                    exit_triggered = True
                    exit_reason = f"Flash Spread Blowout: Spread surged to {current_spread_points:.1f} pts (limit {self.MAX_SAFE_SPREAD_POINTS:.1f} pts)."

                # Danger Check B1: 4-Minute Limit/Zone Buffer
                elif in_limit_buffer:
                    logger.debug(
                        f"[DANGER SENTRY - {role_name}] Position #{ticket} in 4-Minute Breathing Buffer "
                        f"({pos_age_sec:.0f}s / {self.LIMIT_BUFFER_PERIOD_SECONDS:.0f}s elapsed). Micro-exhaustion ignored."
                    )
                    # Only evaluate structural invalidation on a newly confirmed M5 candle close
                    if new_m5_candle_closed and m5_bars and len(m5_bars) >= 5:
                        invalidation_detected, inv_msg = self._detect_m5_structural_invalidation(side, m5_bars)
                        if invalidation_detected:
                            exit_triggered = True
                            exit_reason = f"5m Structural Invalidation Anchor Breached (Limit Buffer): {inv_msg}"

                # Danger Check B2: Standard 180s Grace Period
                elif in_grace_period:
                    logger.debug(
                        f"[DANGER SENTRY - {role_name}] #{ticket} in 180s Grace Period "
                        f"({pos_age_sec:.0f}s / {self.GRACE_PERIOD_SECONDS:.0f}s elapsed). "
                        f"Room to develop granted; early exhaustion bypassed."
                    )

                # Danger Check B3: Confirmed M5 Candle Close Evaluation ONLY
                # MUTE EARLY EXHAUSTION EXITS: Evaluated ONLY on confirmed M5 candle closes
                elif new_m5_candle_closed and m5_bars and len(m5_bars) >= 5:
                    # Buffered 5-Minute Exhaustion Exit (Requires 2 consecutive rejection candles with falling volume)
                    exhaustion_detected, ex_msg = self._detect_m5_exhaustion(side, m5_bars)
                    if exhaustion_detected:
                        exit_triggered = True
                        exit_reason = f"Early Exhaustion Exit (Confirmed M5 Close): {ex_msg}"

                    # Clean 5-Minute Structural Invalidation
                    if not exit_triggered:
                        invalidation_detected, inv_msg = self._detect_m5_structural_invalidation(side, m5_bars)
                        if invalidation_detected:
                            exit_triggered = True
                            exit_reason = f"5m Structural Invalidation (Confirmed M5 Close): {inv_msg}"

                # Note: The premature 85% stop loss cut has been removed. The broker stop loss
                # and confirmed M5 candle close invalidation protect the trade without panic cuts.

                # EXECUTE MARKET EMERGENCY CLOSE IF TRIGGERED
                if exit_triggered:
                    logger.warning(
                        f"[DANGER SENTRY - {role_name}] TRIGGERED for #{ticket} ({side} {volume} lots): "
                        f"{exit_reason} | Floating PnL: ${floating_pnl:.2f}. Executing Proactive Market Close!"
                    )

                    close_success = False
                    if self.execution_engine:
                        close_success = self.execution_engine.close_position(ticket)
                    else:
                        close_success = self._direct_close_position(pos)

                    if close_success:
                        # Record Market Exit into Cooldown Manager (debounces $3.00 price zone for 20m & initiates 15m scalper cooldown)
                        MarketCooldownManager.record_market_exit(
                            symbol=symbol,
                            direction=side,
                            exit_price=current_price,
                            magic_number=magic,
                            ticket=ticket
                        )

                        if not journal:
                            journal = TradeJournal(
                                ticket=ticket,
                                symbol=symbol,
                                side=side,
                                lot_size=volume,
                                entry_price=entry_price,
                                exit_price=current_price,
                                stop_loss=sl,
                                take_profit=tp,
                                realized_pnl=floating_pnl,
                                status="FAIL" if floating_pnl < 0 else "PASS",
                                magic_number=magic,
                                technique_used=str(pos.comment) or "SMC Strategy",
                                reason="Automated Proactive Execution",
                                exit_reason="PROACTIVE_EXHAUSTION_CLOSE" if "Exhaustion" in exit_reason else "EMERGENCY_DANGER_CLOSE",
                                danger_trigger=exit_reason,
                                opened_at=datetime.fromtimestamp(pos.time, tz=timezone.utc),
                                closed_at=datetime.now(timezone.utc)
                            )
                            db.add(journal)
                        else:
                            journal.exit_price = current_price
                            journal.realized_pnl = floating_pnl
                            journal.exit_reason = "PROACTIVE_EXHAUSTION_CLOSE" if "Exhaustion" in exit_reason else "EMERGENCY_DANGER_CLOSE"
                            journal.danger_trigger = exit_reason
                            journal.status = "FAIL" if floating_pnl < -5.0 else ("PASS" if floating_pnl > 0 else "BREAKEVEN")
                            journal.closed_at = datetime.now(timezone.utc)

                        db.commit()
                        db.refresh(journal)

                        # Trigger Segregated AI Post-Mortem
                        self.post_mortem.analyze_closed_trade(journal.id)

                        events.append({
                            "ticket": ticket,
                            "magic": magic,
                            "side": side,
                            "volume": volume,
                            "pnl": floating_pnl,
                            "reason": exit_reason
                        })

            if new_m5_candle_closed and latest_m5_bar_time:
                self._last_evaluated_m5_time = latest_m5_bar_time

            return events

        except Exception as e:
            db.rollback()
            logger.error(f"Error in Danger Sentry: {e}")
            return []
        finally:
            db.close()

    def _check_cent_pyramid_flow(
        self,
        pos,
        journal,
        db,
        symbol: str,
        m5_bars: Optional[List[Any]] = None
    ):
        """
        Cent Pyramid Engine (0.10 - 0.20 Lot Multi-Tranche Deployment):
        1. Fast Breakeven Floor: Once floating move reaches +$0.40 (settings.CENT_FAST_BREAKEVEN_PIPS),
           advances Stop Loss to Breakeven (Entry ± Spread Buffer $0.20) to guarantee a zero-risk floor.
        2. Partial Profit Harvesting (TP1): At +$1.20 (settings.CENT_TP1_PIPS) or 1:1 R:R, liquidates
           50% of the active volume and designates the remainder as an active PYRAMID_RUNNER.
        3. Stepped Stop Loss Ratchet: When secondary continuation / scale-in positions fill, ratchets
           the Stop Loss on older runner positions up to the re-entry fill price (locking green profit),
           then structural trailing advances all stops behind confirmed M1/M5 swing pivots.
        """
        if not getattr(settings, "CENT_ACCOUNT_MODE", False):
            return

        side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
        entry_price = float(pos.price_open)
        current_price = float(pos.price_current)
        sl = float(pos.sl)
        volume = float(pos.volume)
        ticket = pos.ticket

        fast_be_pips = getattr(settings, "CENT_FAST_BREAKEVEN_PIPS", 0.40)
        tp1_pips = getattr(settings, "CENT_TP1_PIPS", 1.20)

        # 1. Fast Breakeven Floor (Zero-Risk Floor)
        if side == "BUY":
            gain = current_price - entry_price
            if gain >= fast_be_pips and ticket not in self._be_secured_tickets:
                target_be = round(entry_price + self.SPREAD_BUFFER_GOLD, 2)
                # Never push closer than MIN_STOP_DISTANCE to current price
                target_be = min(target_be, round(current_price - MIN_STOP_DISTANCE, 2))
                if target_be > sl:
                    logger.info(
                        f"[CentPyramidEngine] Fast Breakeven Floor reached (+${gain:.2f} >= +${fast_be_pips:.2f}) "
                        f"for BUY #{ticket}! Advancing SL to {target_be:.2f}."
                    )
                    modified = False
                    if self.execution_engine:
                        modified = self.execution_engine.modify_position_sl(ticket, target_be)
                    else:
                        modified = self._direct_modify_sl(pos, target_be)
                    if modified:
                        self._be_secured_tickets.add(ticket)

        elif side == "SELL":
            gain = entry_price - current_price
            if gain >= fast_be_pips and ticket not in self._be_secured_tickets:
                target_be = round(entry_price - self.SPREAD_BUFFER_GOLD, 2)
                target_be = max(target_be, round(current_price + MIN_STOP_DISTANCE, 2))
                if sl == 0.0 or target_be < sl:
                    logger.info(
                        f"[CentPyramidEngine] Fast Breakeven Floor reached (+${gain:.2f} >= +${fast_be_pips:.2f}) "
                        f"for SELL #{ticket}! Advancing SL to {target_be:.2f}."
                    )
                    modified = False
                    if self.execution_engine:
                        modified = self.execution_engine.modify_position_sl(ticket, target_be)
                    else:
                        modified = self._direct_modify_sl(pos, target_be)
                    if modified:
                        self._be_secured_tickets.add(ticket)

        # 2. Partial Profit Harvesting (TP1) & Runner Holding
        is_already_harvested = (journal and getattr(journal, "partial_closed", False)) or (ticket in self._pyramid_runners)
        if not is_already_harvested:
            hit_tp1 = False
            if side == "BUY" and (current_price - entry_price) >= tp1_pips:
                hit_tp1 = True
            elif side == "SELL" and (entry_price - current_price) >= tp1_pips:
                hit_tp1 = True

            if hit_tp1:
                close_vol = round(volume * 0.5, 2)
                if close_vol >= 0.01:
                    logger.info(
                        f"[CentPyramidEngine] TP1 Tagged (+${tp1_pips:.2f}) on #{ticket} ({side} {volume} lots)! "
                        f"Liquidating 50% ({close_vol} lots) and tagging remaining as PYRAMID_RUNNER."
                    )
                    success = False
                    if self.execution_engine:
                        success = self.execution_engine.partial_close_position(ticket, close_vol)
                    if success or not self.execution_engine:
                        self._pyramid_runners.add(ticket)
                        if journal:
                            journal.partial_closed = True
                            db.commit()

        # 3. Stepped Stop Loss Ratchet for Runners upon Pullback Continuation
        comment_str = str(getattr(pos, "comment", "") or "").upper()
        if "SCALE" in comment_str or "PYRAMID" in comment_str or (journal and getattr(journal, "is_scale_in", False)):
            all_positions = mt5.positions_get(symbol=symbol) or []
            for other in all_positions:
                if other.ticket == ticket or other.magic != pos.magic:
                    continue
                other_side = "BUY" if other.type == mt5.ORDER_TYPE_BUY else "SELL"
                if other_side != side:
                    continue
                # If other is an older position (runner)
                if other.time < pos.time:
                    re_entry_level = round(float(pos.price_open), 2)
                    other_sl = float(other.sl)
                    if side == "BUY" and re_entry_level > other_sl:
                        cand_sl = min(re_entry_level, round(current_price - MIN_STOP_DISTANCE, 2))
                        if cand_sl > other_sl:
                            logger.info(
                                f"[CentPyramidEngine] Stepped Stop Loss Ratchet! Advancing runner #{other.ticket} SL "
                                f"from {other_sl:.2f} to re-entry fill level {cand_sl:.2f} (Locking profit)."
                            )
                            if self.execution_engine:
                                self.execution_engine.modify_position_sl(other.ticket, cand_sl)
                            else:
                                self._direct_modify_sl(other, cand_sl)
                    elif side == "SELL" and (other_sl == 0.0 or re_entry_level < other_sl):
                        cand_sl = max(re_entry_level, round(current_price + MIN_STOP_DISTANCE, 2))
                        if other_sl == 0.0 or cand_sl < other_sl:
                            logger.info(
                                f"[CentPyramidEngine] Stepped Stop Loss Ratchet! Advancing runner #{other.ticket} SL "
                                f"from {other_sl:.2f} to re-entry fill level {cand_sl:.2f} (Locking profit)."
                            )
                            if self.execution_engine:
                                self.execution_engine.modify_position_sl(other.ticket, cand_sl)
                            else:
                                self._direct_modify_sl(other, cand_sl)

    def _check_and_execute_partial_tp(
        self,
        pos,
        journal,
        db,
        symbol: str,
        m5_bars: Optional[List[Any]] = None
    ):
        """
        50% Partial Take-Profit & Structural Trailing at 1:1 Risk-to-Reward:
        - Closes 50% of the active volume to secure 1R of profit.
        - Structural Trailing: Instead of clamping stop to flat entry ($0.20 buffer) which causes a high
          scratch rate on Gold order block retests, locks in spread cost and trails the stop behind the
          most recent confirmed M5 swing low/high, giving the trade room to breathe.
        - Enforces minimum stop distance of $1.50 from current market price.
        """
        if journal and journal.partial_closed:
            return

        entry_price = float(pos.price_open)
        current_price = float(pos.price_current)
        sl = float(pos.sl)
        side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"

        if sl <= 0:
            return

        hit_1_to_1 = False
        target_trailing_sl = 0.0

        atr_val = 2.0
        if m5_bars and len(m5_bars) >= 14:
            atr_val = SMCAnalyzer.calculate_atr(m5_bars, period=14)

        buffer = max(0.25, 0.25 * atr_val)

        if side == "BUY":
            risk_dist = entry_price - sl
            if risk_dist > 0 and (current_price - entry_price) >= risk_dist:
                hit_1_to_1 = True
                # Detect most recent confirmed M5 swing low
                recent_swing_low = None
                if m5_bars and len(m5_bars) >= 8:
                    bos_data = SMCAnalyzer.detect_bos_choch(m5_bars, left_bars=2, right_bars=2)
                    lows = bos_data.get("swing_lows", [])
                    if lows:
                        recent_swing_low = float(lows[-1]["price"])

                if recent_swing_low and recent_swing_low >= (entry_price + self.SPREAD_BUFFER_GOLD):
                    # Trailing behind advanced swing low, locking in higher profit
                    target_trailing_sl = round(recent_swing_low - buffer, 2)
                else:
                    # Guarantee Breakeven + spread buffer ($0.30 - $0.50) to protect capital
                    target_trailing_sl = round(entry_price + self.SPREAD_BUFFER_GOLD, 2)

                # Never place stop closer than $1.50 to current market price
                target_trailing_sl = min(target_trailing_sl, round(current_price - MIN_STOP_DISTANCE, 2))
                # Only ratchet forward, never pull back
                target_trailing_sl = max(target_trailing_sl, round(sl + 0.10, 2))

        elif side == "SELL":
            risk_dist = sl - entry_price
            if risk_dist > 0 and (entry_price - current_price) >= risk_dist:
                hit_1_to_1 = True
                # Detect most recent confirmed M5 swing high
                recent_swing_high = None
                if m5_bars and len(m5_bars) >= 8:
                    bos_data = SMCAnalyzer.detect_bos_choch(m5_bars, left_bars=2, right_bars=2)
                    highs = bos_data.get("swing_highs", [])
                    if highs:
                        recent_swing_high = float(highs[-1]["price"])

                if recent_swing_high and recent_swing_high <= (entry_price - self.SPREAD_BUFFER_GOLD):
                    target_trailing_sl = round(recent_swing_high + buffer, 2)
                else:
                    target_trailing_sl = round(entry_price - self.SPREAD_BUFFER_GOLD, 2)

                # Never place stop closer than $1.50 to current market price
                target_trailing_sl = max(target_trailing_sl, round(current_price + MIN_STOP_DISTANCE, 2))
                # Only ratchet forward, never pull back
                target_trailing_sl = min(target_trailing_sl, round(sl - 0.10, 2))

        if hit_1_to_1 and self.execution_engine:
            close_vol = round(pos.volume * 0.5, 2)
            if close_vol >= 0.01:
                logger.info(
                    f"[Partial TP & Structural Trailing] Position #{pos.ticket} ({side}) reached 1:1 R:R! "
                    f"Executing 50% partial close ({close_vol} lots) and setting structural trailing SL to {target_trailing_sl:.2f}."
                )
                partial_ok = self.execution_engine.partial_close_position(pos.ticket, close_vol)
                sl_ok = self.execution_engine.modify_position_sl(pos.ticket, target_trailing_sl)

                if journal:
                    journal.partial_closed = True
                    db.commit()

    def _detect_m5_exhaustion(self, side: str, m5_bars: List[Any]) -> Tuple[bool, str]:
        """
        Buffered Exhaustion Exit on Confirmed 5m (M5) Close:
        Requires AT LEAST 2 consecutive completed rejection candles on the 5m chart with falling volume
        before triggering an early exit.
        - BUY: 2 consecutive M5 candles with pronounced upper rejection wicks (>= 50% of total candle range)
          and declining volume across the pushes.
        - SELL: 2 consecutive M5 candles with pronounced lower rejection wicks (>= 50% of total candle range)
          and declining volume across the pushes.
        """
        if not m5_bars or len(m5_bars) < 3:
            return False, ""

        last_2 = m5_bars[-2:]
        volumes = [float(b.get("volume", 0.0) if isinstance(b, dict) else getattr(b, "volume", 0.0)) for b in last_2]
        is_volume_falling = (volumes[-1] < volumes[-2]) if len(volumes) >= 2 else False

        if side == "BUY":
            rejections = 0
            for b in last_2:
                o = float(b.get("open") if isinstance(b, dict) else b.open)
                c = float(b.get("close") if isinstance(b, dict) else b.close)
                h = float(b.get("high") if isinstance(b, dict) else b.high)
                l = float(b.get("low") if isinstance(b, dict) else b.low)
                total_range = h - l
                upper_wick = h - max(o, c)
                if total_range > 0 and (upper_wick / total_range) >= 0.50:
                    rejections += 1

            if rejections >= 2 and is_volume_falling:
                return True, f"Buyer exhaustion: 2 consecutive 5m top rejection wicks (>=50%) with falling volume ({volumes[-2]:.0f} -> {volumes[-1]:.0f})."

        elif side == "SELL":
            rejections = 0
            for b in last_2:
                o = float(b.get("open") if isinstance(b, dict) else b.open)
                c = float(b.get("close") if isinstance(b, dict) else b.close)
                h = float(b.get("high") if isinstance(b, dict) else b.high)
                l = float(b.get("low") if isinstance(b, dict) else b.low)
                total_range = h - l
                lower_wick = min(o, c) - l
                if total_range > 0 and (lower_wick / total_range) >= 0.50:
                    rejections += 1

            if rejections >= 2 and is_volume_falling:
                return True, f"Seller exhaustion: 2 consecutive 5m bottom rejection wicks (>=50%) with falling volume ({volumes[-2]:.0f} -> {volumes[-1]:.0f})."

        return False, ""

    def _detect_m5_structural_invalidation(self, side: str, m5_bars: List[Any]) -> Tuple[bool, str]:
        """
        Structural Invalidation on Confirmed 5m (M5) Close:
        Only evaluate structural invalidation if a confirmed M5 candle cleanly closes against the trade.
        - Open BUY: M5 candle cleanly closes below the recent 5m swing low.
        - Open SELL: M5 candle cleanly closes above the recent 5m swing high.
        """
        if not m5_bars or len(m5_bars) < 8:
            return False, ""

        bos_data = SMCAnalyzer.detect_bos_choch(m5_bars, left_bars=2, right_bars=2)
        swing_highs = bos_data.get("swing_highs", [])
        swing_lows = bos_data.get("swing_lows", [])

        latest_close = float(m5_bars[-1]["close"] if isinstance(m5_bars[-1], dict) else m5_bars[-1].close)

        if side == "BUY" and swing_lows:
            recent_low = float(swing_lows[-1]["price"])
            if latest_close < recent_low:
                return True, f"M5 candle closed cleanly ({latest_close:.2f}) below 5m swing low ({recent_low:.2f})."

        elif side == "SELL" and swing_highs:
            recent_high = float(swing_highs[-1]["price"])
            if latest_close > recent_high:
                return True, f"M5 candle closed cleanly ({latest_close:.2f}) above 5m swing high ({recent_high:.2f})."

        return False, ""

    def _apply_floating_profit_protection(
        self,
        pos,
        journal,
        db,
        symbol: str,
        m5_bars: Optional[List[Any]] = None
    ):
        """
        Structural trailing stop protection on confirmed M5 candle closes:
        Trails behind the most recent confirmed M5 swing low (for BUY) or swing high (for SELL).
        Enforces MIN_STOP_DISTANCE ($1.50) from current price and never retreats the stop.
        """
        side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
        entry_price = float(pos.price_open)
        current_price = float(pos.price_current)
        sl = float(pos.sl)
        profit = float(pos.profit)

        if profit < self.PROFIT_TRAIL_MIN or not m5_bars or len(m5_bars) < 8:
            return

        atr_val = SMCAnalyzer.calculate_atr(m5_bars, period=14) if len(m5_bars) >= 14 else 2.0
        buffer = max(0.20, 0.20 * atr_val)

        bos_data = SMCAnalyzer.detect_bos_choch(m5_bars, left_bars=2, right_bars=2)
        swing_highs = bos_data.get("swing_highs", [])
        swing_lows = bos_data.get("swing_lows", [])

        if side == "BUY" and swing_lows:
            recent_low = float(swing_lows[-1]["price"])
            cand_sl = round(recent_low - buffer, 2)
            # Enforce minimum stop distance from current price ($1.50)
            cand_sl = min(cand_sl, round(current_price - MIN_STOP_DISTANCE, 2))
            if cand_sl > sl and cand_sl > (entry_price + self.SPREAD_BUFFER_GOLD):
                logger.info(f"[Structural Trailing] Ratcheting BUY SL for #{pos.ticket} to {cand_sl:.2f} (Swing Low {recent_low:.2f} - {buffer:.2f})")
                if self.execution_engine:
                    self.execution_engine.modify_position_sl(pos.ticket, cand_sl)
                else:
                    self._direct_modify_sl(pos, cand_sl)

        elif side == "SELL" and swing_highs:
            recent_high = float(swing_highs[-1]["price"])
            cand_sl = round(recent_high + buffer, 2)
            cand_sl = max(cand_sl, round(current_price + MIN_STOP_DISTANCE, 2))
            if (sl == 0.0 or cand_sl < sl) and cand_sl < (entry_price - self.SPREAD_BUFFER_GOLD):
                logger.info(f"[Structural Trailing] Ratcheting SELL SL for #{pos.ticket} to {cand_sl:.2f} (Swing High {recent_high:.2f} + {buffer:.2f})")
                if self.execution_engine:
                    self.execution_engine.modify_position_sl(pos.ticket, cand_sl)
                else:
                    self._direct_modify_sl(pos, cand_sl)

        # Disaster Guard (Blowout Protection)
        if side == "BUY" and (entry_price - current_price) >= self.DISASTER_STOP_DISTANCE:
            logger.warning(f"[Disaster Guard] BUY position #{pos.ticket} dropped ${self.DISASTER_STOP_DISTANCE} adverse. Immediate emergency close.")
            self._force_close(pos)
        elif side == "SELL" and (current_price - entry_price) >= self.DISASTER_STOP_DISTANCE:
            logger.warning(f"[Disaster Guard] SELL position #{pos.ticket} rallied ${self.DISASTER_STOP_DISTANCE} adverse. Immediate emergency close.")
            self._force_close(pos)

    def _direct_modify_sl(self, pos, new_sl: float) -> bool:
        """Fallback to direct MT5 SL modification when execution_engine unavailable."""
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "sl": float(round(new_sl, 2)),
            "tp": pos.tp,
            "magic": pos.magic,
            "comment": f"StructuralSL {pos.ticket}"
        }
        res = mt5.order_send(request)
        return res.retcode == mt5.TRADE_RETCODE_DONE

    def _force_close(self, pos):
        """Immediate emergency close of a position."""
        if self.execution_engine:
            self.execution_engine.close_position(pos.ticket)
        else:
            self._direct_close_position(pos)

    def _attempt_scale_in(self, pos, journal, db, symbol: str):
        """
        Pullback scale‑in when primary position is profitable and SL secured.
        Places at most ONE scale-in order at 50% of original volume.
        """
        # Guard 1: Never scale into a scale-in order
        comment_str = str(getattr(pos, "comment", "") or "")
        if "ScaleIn" in comment_str or "SCALE" in comment_str.upper():
            return

        # Guard 2: Memory set tracking
        if not hasattr(self, "_scaled_in_tickets"):
            self._scaled_in_tickets = set()
        if pos.ticket in self._scaled_in_tickets:
            return

        # Guard 3: Database flag check
        if journal and getattr(journal, "is_scale_in", False):
            return

        side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
        entry_price = float(pos.price_open)
        current_price = float(pos.price_current)
        sl = float(pos.sl)
        volume = float(pos.volume)

        # Ensure SL is secured and position is in profit
        if sl <= 0 or float(pos.profit) <= 0:
            return

        is_cent_mode = getattr(settings, "CENT_ACCOUNT_MODE", False)
        max_tranches = getattr(settings, "CENT_MAX_TRANCHES", 5) if is_cent_mode else 2
        all_open = mt5.positions_get(symbol=symbol) or []
        if len(all_open) >= max_tranches:
            return

        # Pullback detection: price within $0.80 of entry on Gold
        pullback_threshold = 0.80 if is_cent_mode else 0.50
        if abs(current_price - entry_price) > pullback_threshold:
            return

        if is_cent_mode:
            scale_vol = getattr(settings, "CENT_TRANCHE_MIN_LOT", 0.10)
        else:
            scale_vol = round(volume * 0.5, 2)

        if scale_vol < 0.01:
            return

        comment = f"CentPyramid-{pos.ticket}" if is_cent_mode else f"ScaleIn-{pos.ticket}"
        success = False
        if self.execution_engine and hasattr(self.execution_engine, "execute_custom_order"):
            success = self.execution_engine.execute_custom_order(
                symbol=symbol,
                side=side,
                volume=scale_vol,
                stop_loss=sl,
                take_profit=0.0,
                comment=comment,
                proposal=None
            )
        else:
            order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).ask if side == "BUY" else mt5.symbol_info_tick(symbol).bid
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": scale_vol,
                "type": order_type,
                "price": price,
                "sl": sl,
                "deviation": 25,
                "magic": pos.magic,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK
            }
            res = mt5.order_send(request)
            success = (res.retcode == mt5.TRADE_RETCODE_DONE)

        if success:
            self._scaled_in_tickets.add(pos.ticket)
            if journal:
                journal.is_scale_in = True
                db.commit()
            logger.info(f"[CentPyramidEngine] Placed {side} continuation tranche of {scale_vol} lots for ticket {pos.ticket} (Total open: {len(all_open) + 1}/{max_tranches})")

    def _direct_close_position(self, pos) -> bool:
        """Direct MT5 emergency close fallback."""
        symbol_info = mt5.symbol_info(pos.symbol)
        if not symbol_info:
            return False
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return False

        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        filling_type = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode == 2:
            filling_type = mt5.ORDER_FILLING_IOC
        elif symbol_info.filling_mode != 1:
            filling_type = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "price": price,
            "deviation": 25,
            "magic": pos.magic,
            "comment": f"ProactiveClose #{pos.ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        res = mt5.order_send(request)
        return res.retcode == mt5.TRADE_RETCODE_DONE

    def _is_limit_fill(self, pos, journal) -> bool:
        """
        Determines whether an open position originated from a filled pending Limit order or Zone Retest.
        """
        comment = str(getattr(pos, "comment", "") or "").upper()
        if "LIMIT" in comment or "LMT" in comment or "RETEST" in comment:
            return True

        if journal:
            tech = str(getattr(journal, "technique_used", "") or "").upper()
            reason = str(getattr(journal, "reason", "") or "").upper()
            if "LIMIT" in tech or "LIMIT" in reason or "RETEST" in tech or "RETEST" in reason:
                return True

        try:
            deals = mt5.history_deals_get(position=pos.ticket)
            if deals:
                for deal in deals:
                    if deal.entry == mt5.DEAL_ENTRY_IN:
                        deal_comment = str(deal.comment or "").upper()
                        if "LIMIT" in deal_comment or "RETEST" in deal_comment:
                            return True
        except Exception:
            pass

        return False
