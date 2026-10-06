import logging
from typing import List, Dict, Any, Optional, Tuple
import MetaTrader5 as mt5

from app.db.session import SessionLocal
from app.models.strategy import TradingHypothesis
from app.models.trading import Position, Order
from app.agents.trade_proposal import TradeProposal
from app.risk.capital_manager import CapitalManager, AccountTier
from app.risk.market_filters import MarketFilters
from app.risk.stop_policy import enforce_min_stop, MIN_STOP_DISTANCE

logger = logging.getLogger(__name__)

class ChiefRiskArbiter:
    """
    Chief Risk & Execution Arbiter (The Gatekeeper):
    - Timeframe agents NEVER execute orders directly.
    - Arbiter receives trade proposals, evaluates market conditions, tier rules, and risk budgets.
    - Decides whether to approve, arbitrate concurrent proposals, or strictly reject.
    - Executes approved proposals via MT5 with mathematically exact lot sizes.
    """

    def __init__(self, execution_engine=None, zone_monitor=None):
        self.capital_manager = CapitalManager()
        self.market_filters = MarketFilters()
        self.execution_engine = execution_engine
        self.zone_monitor = zone_monitor

    def arbitrate_proposals(
        self,
        proposals: List[TradeProposal],
        macro_regime: Dict[str, Any],
        symbol: str = "XAUUSD"
    ) -> List[Tuple[TradeProposal, float, str]]:
        """
        Evaluates a batch of candidate proposals from Scout and Sniper.
        Returns: List of tuples: (approved_proposal, approved_lot_size, execution_notes)
        """
        if not proposals:
            return []

        approved_trades = []
        db = SessionLocal()

        try:
            # 1. Fetch current account equity and open position count
            account_info = mt5.account_info()
            if not account_info:
                logger.error("Arbiter could not retrieve MT5 account info. Halting execution.")
                return []

            current_equity = float(account_info.equity)
            open_positions = mt5.positions_get(symbol=symbol) or []
            open_count = len(open_positions)

            pending_orders = mt5.orders_get(symbol=symbol) or []
            active_limits = [o for o in pending_orders if o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT)]
            active_limit_count = len(active_limits)

            tier = AccountTier.get_tier(current_equity)
            logger.info(
                f"[Chief Arbiter Gatekeeper] Current Equity: ${current_equity:.2f} ({tier.tier_name}) | "
                f"Active Positions: {open_count} | Active Limits: {active_limit_count}"
            )

            # 2. Check Circuit Breaker
            is_cb_tripped, cb_reason = self.capital_manager.check_circuit_breaker(current_equity)
            if is_cb_tripped:
                logger.warning(f"[Arbiter] REJECTED ALL: {cb_reason}")
                for p in proposals:
                    self._record_hypothesis_decision(db, p, "REJECTED_CIRCUIT_BREAKER", cb_reason)
                return []

            # 3. Check Shared Margin Guard (<= 20% margin utilization across both magic numbers)
            if self.execution_engine and hasattr(self.execution_engine, "check_shared_margin_guard"):
                margin_ok, margin_pct, margin_msg = self.execution_engine.check_shared_margin_guard()
                if not margin_ok:
                    logger.warning(f"[Arbiter] REJECTED ALL: {margin_msg}")
                    for p in proposals:
                        self._record_hypothesis_decision(db, p, "REJECTED_SHARED_MARGIN_GUARD", margin_msg)
                    return []

            # 4. Check Real-Time Spread Guard (Max 50 points / $0.50 on Gold)
            spread_ok, spread_pts, spread_reason = self.market_filters.check_spread_guard(symbol)
            if not spread_ok:
                logger.warning(f"[Arbiter] REJECTED ALL: {spread_reason}")
                for p in proposals:
                    self._record_hypothesis_decision(db, p, "REJECTED_SPREAD_GUARD", spread_reason)
                return []

            # 5. Check Macro News Blackout (30m prior, 15m post high-impact USD events)
            news_ok, news_reason = self.market_filters.check_news_blackout(symbol)
            if not news_ok:
                logger.warning(f"[Arbiter] REJECTED ALL: {news_reason}")
                for p in proposals:
                    self._record_hypothesis_decision(db, p, "REJECTED_NEWS_BLACKOUT", news_reason)
                return []

            # 6. Concurrency Ceiling: Unlimited concurrent trades allowed by user policy
            MAX_ACTIVE_LIMITS = 999
            MAX_OPEN_POSITIONS = 999

            available_limit_slots = 999
            available_market_slots = 999

            # 7. Resolve Price Zone Debounce & Macro Regime Alignment
            from app.agent.post_mortem import PostMortemEngine
            from app.agent.experience_engine import ExperienceEngine
            from app.trading.cooldown_manager import MarketCooldownManager

            valid_proposals = []
            for p in proposals:
                is_limit = getattr(p, "order_execution_type", "LIMIT") == "LIMIT"

                # Gate 7A: Setup Invalidation Price Zone Debounce ($3.00 zone / 20 min blacklist)
                is_debounced, deb_sec, deb_msg = MarketCooldownManager.check_price_zone_debounce(
                    symbol=p.symbol,
                    direction=p.direction,
                    proposed_price=p.entry_price
                )
                if is_debounced:
                    logger.warning(f"[Arbiter] REJECTED Proposal {p.proposal_id}: {deb_msg}")
                    self._record_hypothesis_decision(db, p, "REJECTED_ZONE_DEBOUNCE", deb_msg)
                    continue

                # Gate 7B: Scalper Mandatory 15-Minute Exit Cooldown
                if p.magic_number == 2002:
                    is_cooling_down, rem_sec, cool_msg = MarketCooldownManager.check_scalper_cooldown()
                    if is_cooling_down:
                        logger.warning(f"[Arbiter] REJECTED Scalper Proposal {p.proposal_id}: {cool_msg}")
                        self._record_hypothesis_decision(db, p, "REJECTED_SCALPER_COOLDOWN", cool_msg)
                        continue

                # Gate 7C: Swing Trader HTF Macro Directional Alignment
                # Scalp Trader (Magic 2002) is EXEMPT from Swing HTF macro directional veto
                if p.magic_number == 1001:
                    h1_trend = str(macro_regime.get("h1_trend") or "").upper()
                    # Allow longs if macro_regime allows long OR H1 trend is Bullish OR Alpha Vantage yields approve swing long
                    allow_long = macro_regime.get("allow_long", True) or ("BULLISH" in h1_trend) or macro_regime.get("allow_gold_swing_buy", False)
                    # Allow shorts if macro_regime allows short OR H1 trend is Bearish
                    allow_short = macro_regime.get("allow_short", True) or ("BEARISH" in h1_trend)

                    if p.direction == "BUY" and not allow_long:
                        self._record_hypothesis_decision(
                            db, p, "REJECTED_MACRO_ALIGNMENT",
                            f"BUY proposal contradicts HTF Macro Regime ({macro_regime.get('regime')})"
                        )
                        continue
                    if p.direction == "SELL" and not allow_short:
                        self._record_hypothesis_decision(
                            db, p, "REJECTED_MACRO_ALIGNMENT",
                            f"SELL proposal contradicts HTF Macro Regime ({macro_regime.get('regime')})"
                        )
                        continue

                # Gate 7D: PRE-ORDER EXPERIENCE VALIDATION GATE (experience_matrix.json)
                setup_type = getattr(p, "setup_cluster", "M5_FVG_LIMIT") or "M5_FVG_LIMIT"
                cluster_info = ExperienceEngine.get_setup_cluster(setup_type)
                conf_score = float(cluster_info.get("confidence_score", 0.70))
                attempts = int(cluster_info.get("total_attempts", 0))

                # If historical confidence score for this pattern is < 60%, require H1 directional confirmation
                if conf_score < 0.60:
                    h1_trend = str(macro_regime.get("h1_trend") or macro_regime.get("regime", "")).upper()
                    h1_aligned = False
                    if p.direction == "BUY" and ("BULLISH" in h1_trend or macro_regime.get("allow_long", False)):
                        h1_aligned = True
                    elif p.direction == "SELL" and ("BEARISH" in h1_trend or macro_regime.get("allow_short", False)):
                        h1_aligned = True

                    if not h1_aligned:
                        reject_msg = (
                            f"Pre-Order Experience Gate REJECTED: Setup pattern '{setup_type}' historical confidence is "
                            f"{conf_score*100:.1f}% (< 60% across {attempts} trades) and lacks H1 directional confirmation (H1 Trend: {h1_trend})."
                        )
                        logger.warning(f"[Arbiter] {reject_msg}")
                        self._record_hypothesis_decision(db, p, "REJECTED_EXPERIENCE_GATE", reject_msg)
                        continue
                    else:
                        logger.info(
                            f"[Arbiter Pre-Order Gate] Setup '{setup_type}' confidence is {conf_score*100:.1f}% (<60%). "
                            f"APPROVED via confirmed H1 directional alignment ({h1_trend})."
                        )

                # If confidence score is >= 75%, approve order immediately with dynamic lot sizing
                if conf_score >= 0.75 and attempts >= 2:
                    p.risk_pct = min(p.risk_pct * 1.15, 0.02)
                    logger.info(
                        f"[Arbiter Pre-Order Gate] High-Confidence Setup '{setup_type}' ({conf_score*100:.1f}% >= 75%). "
                        f"FAST-TRACKED with dynamic lot sizing (Risk: {p.risk_pct*100:.2f}%)."
                    )

                # Check Scalper Quantitative Circuit Breaker (30m freeze on 2 losses)
                if p.magic_number == 2002:
                    is_frozen, effective_scalp_risk, freeze_msg = PostMortemEngine.check_scalper_circuit_breakers(current_equity)
                    if is_frozen:
                        logger.warning(f"[Arbiter] REJECTED Scalper Proposal {p.proposal_id}: {freeze_msg}")
                        self._record_hypothesis_decision(db, p, "REJECTED_SCALPER_FREEZE", freeze_msg)
                        continue
                    p.risk_pct = min(p.risk_pct, effective_scalp_risk)

                valid_proposals.append(p)

            if not valid_proposals:
                return []

            # Sort candidate proposals descending by confluence score
            valid_proposals.sort(key=lambda x: x.confluence_score, reverse=True)

            # Select candidates respecting available concurrency ceilings
            selected_candidates = []
            cur_limit_slots = available_limit_slots
            cur_market_slots = available_market_slots

            for cand in valid_proposals:
                is_cand_limit = getattr(cand, "order_execution_type", "LIMIT") == "LIMIT"
                if is_cand_limit:
                    if cur_limit_slots > 0:
                        selected_candidates.append(cand)
                        cur_limit_slots -= 1
                else:
                    if cur_market_slots > 0:
                        selected_candidates.append(cand)
                        cur_market_slots -= 1

            # 8. Sizing & Micro-Account Trap Guard per selected candidate
            symbol_info = mt5.symbol_info(symbol)
            point = symbol_info.point if symbol_info else 0.01
            tick_val = 1.0  # $1.00 per point per lot on Gold
            min_lot = symbol_info.volume_min if symbol_info else 0.01
            step_lot = symbol_info.volume_step if symbol_info else 0.01
            max_lot = symbol_info.volume_max if symbol_info else 100.0

            for candidate in selected_candidates:
                # Isolated Position Tracking by magic_number
                same_magic_positions = [p for p in open_positions if p.magic == candidate.magic_number]
                target_type = mt5.ORDER_TYPE_BUY if candidate.direction == "BUY" else mt5.ORDER_TYPE_SELL
                opposite_type = mt5.ORDER_TYPE_SELL if candidate.direction == "BUY" else mt5.ORDER_TYPE_BUY

                same_dir_positions = [p for p in same_magic_positions if p.type == target_type]
                opp_dir_positions = [p for p in same_magic_positions if p.type == opposite_type]

                # Check 1: Avoid stacked duplicate entries within $2.00 for SAME magic number (unless scale-in)
                if same_dir_positions and not candidate.is_scale_in:
                    last_entry = same_dir_positions[-1].price_open
                    if abs(candidate.entry_price - last_entry) < 2.0:
                        logger.info(
                            f"[Arbiter] Skipping duplicate {candidate.direction} entry within $2.00 of existing position "
                            f"#{same_dir_positions[-1].ticket} (Magic {candidate.magic_number})"
                        )
                        continue

                # Check 2: Within SAME magic number, avoid contradictory opposing positions
                # (Notice: Swing 1001 and Scalp 2002 are segregated and can co-exist!)
                if opp_dir_positions:
                    if candidate.confluence_score >= 80.0:
                        logger.info(
                            f"[Arbiter] High-confluence reversal ({candidate.confluence_score}) on magic {candidate.magic_number}! "
                            f"Closing {len(opp_dir_positions)} opposing position(s) before reversing."
                        )
                        if self.execution_engine:
                            for op in opp_dir_positions:
                                self.execution_engine.close_position(op.ticket)
                    else:
                        logger.info(
                            f"[Arbiter] Opposing position #{opp_dir_positions[0].ticket} active for magic {candidate.magic_number}. "
                            f"Rejecting counter-trend proposal {candidate.proposal_id}."
                        )
                        self._record_hypothesis_decision(
                            db, candidate, "REJECTED_HEDGING_CONFLICT",
                            f"Opposing position held for magic {candidate.magic_number}"
                        )
                        continue

                # Enforce institutional minimum stop distance ($1.50) before sizing
                candidate.structural_sl, widened = enforce_min_stop(
                    candidate.direction, candidate.entry_price, candidate.structural_sl, MIN_STOP_DISTANCE
                )
                if widened:
                    logger.info(
                        f"[Arbiter] Widened SL for {candidate.proposal_id} to {candidate.structural_sl} "
                        f"to enforce minimum ${MIN_STOP_DISTANCE:.2f} stop distance on Gold."
                    )

                # Sizing: Scale-in Pyramiding vs Standard Risk Sizing
                if candidate.is_scale_in and candidate.parent_ticket:
                    parent_pos = [p for p in open_positions if p.ticket == candidate.parent_ticket]
                    if parent_pos:
                        lot_size = round(parent_pos[0].volume * 0.5, 2)
                    else:
                        lot_size = 0.01
                    lot_size = max(min_lot, min(lot_size, max_lot))
                    trap_err = None
                else:
                    lot_size, trap_err = self.capital_manager.calculate_lot_size(
                        current_equity=current_equity,
                        entry_price=candidate.entry_price,
                        stop_loss=candidate.structural_sl,
                        point=point,
                        tick_value_per_point=tick_val,
                        volume_min=min_lot,
                        volume_max=max_lot,
                        volume_step=step_lot,
                        risk_pct_override=candidate.risk_pct
                    )

                if lot_size is None or trap_err is not None:
                    # Micro-Account Trap Guard or invalid stop
                    logger.warning(f"[Arbiter] REJECTED Proposal {candidate.proposal_id}: {trap_err}")
                    self._record_hypothesis_decision(db, candidate, "REJECTED_MICRO_TRAP", trap_err or "Lot size error")
                    continue

                # Passed all institutional gates!
                exec_note = (
                    f"APPROVED by Arbiter: Magic={candidate.magic_number}, Risk={candidate.risk_pct*100:.2f}%, "
                    f"LotSize={lot_size}, Type={getattr(candidate, 'order_execution_type', 'LIMIT')}, "
                    f"Confluence={candidate.confluence_score}, ScaleIn={candidate.is_scale_in}."
                )
                logger.info(
                    f"[Arbiter] APPROVED Trade: {getattr(candidate, 'order_execution_type', 'LIMIT')} {candidate.direction} "
                    f"{symbol} {lot_size} lots at {candidate.entry_price} (Magic {candidate.magic_number})"
                )
                self._record_hypothesis_decision(db, candidate, "APPROVED", exec_note)
                approved_trades.append((candidate, lot_size, exec_note))

            return approved_trades

        except Exception as e:
            logger.error(f"Error during Arbiter proposal evaluation: {e}")
            return []
        finally:
            db.close()

    def execute_approved_trades(self, approved_list: List[Tuple[TradeProposal, float, str]], symbol: str = "XAUUSD") -> int:
        """
        Routes approved proposals to MT5 execution engine or ZoneRetestMonitor.
        Supports dynamic ZONE_RETEST confirmation, pending limit orders, and direct market orders.
        Returns count of executed / armed orders.
        """
        if not approved_list or not self.execution_engine:
            return 0

        executed_count = 0
        for proposal, lot_size, note in approved_list:
            order_type = getattr(proposal, "order_execution_type", "LIMIT")
            cluster = getattr(proposal, "setup_cluster", "LIMIT")

            # Route 1: Dynamic Zone-Retest Confirmation (Scalp Trader)
            if order_type == "ZONE_RETEST" and self.zone_monitor:
                success = self.zone_monitor.arm_zone(proposal, lot_size, note)
                if success:
                    executed_count += 1
                    logger.info(
                        f"[Arbiter] Successfully armed ZONE_RETEST for {proposal.direction} "
                        f"[{proposal.zone_low} - {proposal.zone_high}] (Magic: {proposal.magic_number})"
                    )
                else:
                    logger.error(f"[Arbiter] Failed to arm ZONE_RETEST for {proposal.proposal_id}")
                continue

            # Route 2: Scale-In or Standard Limit / Market Orders
            if proposal.is_scale_in:
                comment = f"ScaleIn #{proposal.parent_ticket or ''}"[:31]
            elif order_type == "LIMIT":
                comment = f"LMT-{cluster[:12]}-{proposal.proposal_id[:6]}"[:31]
            elif proposal.magic_number == 1001:
                comment = f"Swing-{proposal.proposal_id[:6]}"
            else:
                comment = f"Scalp-{proposal.proposal_id[:6]}"

            if order_type == "LIMIT" and hasattr(self.execution_engine, "execute_custom_limit_order"):
                success = self.execution_engine.execute_custom_limit_order(
                    symbol=symbol,
                    side=proposal.direction,
                    limit_price=proposal.entry_price,
                    volume=lot_size,
                    stop_loss=proposal.structural_sl,
                    take_profit=proposal.suggested_tp,
                    comment=comment,
                    proposal=proposal,
                    ttl_minutes=getattr(proposal, "ttl_minutes", 20)
                )
            else:
                success = self.execution_engine.execute_custom_order(
                    symbol=symbol,
                    side=proposal.direction,
                    volume=lot_size,
                    stop_loss=proposal.structural_sl,
                    take_profit=proposal.suggested_tp,
                    comment=comment,
                    proposal=proposal
                )

            if success:
                executed_count += 1
                logger.info(
                    f"[Arbiter] Successfully routed {order_type} "
                    f"{proposal.direction} {lot_size} lots to MT5 for proposal {proposal.proposal_id} "
                    f"(Magic: {proposal.magic_number})"
                )
            else:
                logger.error(f"[Arbiter] Failed to route order to MT5 for proposal {proposal.proposal_id}")

        return executed_count

    def _record_hypothesis_decision(self, db, proposal: TradeProposal, status: str, notes: str):
        """Audit logging to database table trading_hypotheses."""
        try:
            hyp = TradingHypothesis(
                symbol=proposal.symbol,
                strategy_type=f"{proposal.agent_role}_{proposal.timeframe}",
                direction=proposal.direction,
                confidence=round(proposal.confluence_score / 100.0, 2),
                thesis=proposal.thesis,
                supporting_evidence="; ".join(proposal.supporting_confluences) + f" | Notes: {notes}",
                counter_evidence=proposal.invalidation_condition,
                entry_price_estimate=proposal.entry_price,
                suggested_stop_loss=proposal.structural_sl,
                suggested_take_profit=proposal.suggested_tp,
                status=status
            )
            db.add(hyp)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to record hypothesis audit record: {e}")
