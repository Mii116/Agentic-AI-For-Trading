import logging
import uuid
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import MetaTrader5 as mt5
from google import genai
from google.genai import types

from app.config.settings import settings
from app.db.session import SessionLocal
from app.models.trading import TradeJournal
from app.indicators.smc import SMCAnalyzer
from app.agents.trade_proposal import TradeProposal
from app.trading.macro_worker import AlphaVantageMacroWorker

logger = logging.getLogger(__name__)

class SwingTrader:
    """
    Swing Trader ("The Institutional Partner") — Magic: 1001
    - Timeframe Stack:
        * D1 & H4: Directional flow (200 EMA + market structure)
        * H1: Context & Key Zones (liquidity pools and order blocks)
        * 30m & 15m: Confirmation & Execution (BOS / CHoCH)
    - Trade Direction: Long and Short capability
    - Risk Allocation: 1.5% to 2.0% equity risk per trade
    - Stop Loss Sizing: Structural invalidation anchored behind H1/30m swing levels ($4.00–$8.00 on Gold)
    - Macro Gate Requirement: Must pass Alpha Vantage macro filter before approving BUY setups
    - Post-Mortem Pre-Trade Gate: Queries last 3 failed trades for Magic 1001 and performs explicit self-check
    """

    MAGIC_NUMBER = 1001
    BASE_RISK_PCT = 0.015  # 1.5% base risk per trade

    def __init__(self):
        self.macro_worker = AlphaVantageMacroWorker()
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def _query_past_failures(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Queries the trade_journals table for the last 3 failed trades for Magic 1001."""
        db = SessionLocal()
        try:
            failures = db.query(TradeJournal).filter(
                TradeJournal.magic_number == self.MAGIC_NUMBER,
                (TradeJournal.status == "FAIL") | (TradeJournal.realized_pnl < -5.0)
            ).order_by(TradeJournal.closed_at.desc()).limit(limit).all()

            results = []
            for f in failures:
                results.append({
                    "ticket": f.ticket,
                    "side": f.side,
                    "entry_price": f.entry_price,
                    "exit_price": f.exit_price,
                    "realized_pnl": f.realized_pnl,
                    "why_it_went_wrong": f.why_it_went_wrong or "Adverse structural move hit invalidation.",
                    "lessons_learned": f.lessons_learned or "Respect invalidation boundaries and avoid chasing momentum.",
                    "danger_trigger": f.danger_trigger or "N/A",
                    "technique_used": f.technique_used
                })
            return results
        except Exception as e:
            logger.error(f"Error querying past failures for Swing Trader: {e}")
            return []
        finally:
            db.close()

    def _perform_pre_trade_self_check(self, proposal: TradeProposal, failures: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Explicit Pre-Trade Gate:
        Evaluates candidate proposal against the [ANTI-PATTERNS & RECENT MISTAKES TO AVOID] block.
        If the trade repeats any recent mistake pattern, rejects it immediately.
        """
        if not failures:
            return True, "No recent failed trades on record for Swing Trader (1001)."

        failures_text = ""
        for i, f in enumerate(failures, 1):
            failures_text += (
                f"\nFailure #{i} (Ticket #{f['ticket']} | {f['side']} at {f['entry_price']} | Loss: ${abs(f['realized_pnl'] or 0):.2f}):\n"
                f"  - Why It Went Wrong: {f['why_it_went_wrong']}\n"
                f"  - Lesson Learned: {f['lessons_learned']}\n"
                f"  - Trigger: {f['danger_trigger']}\n"
            )

        if settings.ENABLE_CLOUD_AI and self.client:
            prompt = f"""
            You are the Lead Quantitative Risk Auditor reviewing a proposed trade setup for SWING TRADER (Magic: {self.MAGIC_NUMBER}).
            
            === PROPOSED SWING TRADE ===
            - Symbol: {proposal.symbol}
            - Direction: {proposal.direction}
            - Entry Price: {proposal.entry_price}
            - Stop Loss: {proposal.structural_sl}
            - Take Profit: {proposal.suggested_tp}
            - Thesis: {proposal.thesis}
            - Supporting Confluences: {', '.join(proposal.supporting_confluences)}

            === [ANTI-PATTERNS & RECENT MISTAKES TO AVOID] ===
            The following 3 recent trades for Magic {self.MAGIC_NUMBER} resulted in losses:
            {failures_text}

            === CRITICAL SELF-CHECK DIRECTIVE ===
            Perform an explicit pre-trade self-check. Does this candidate trade repeat ANY of the 3 recent failure patterns above?
            Specifically REJECT (passed=false) if:
            1. It enters in the same direction into a price zone where a recent swing setup failed or broke down.
            2. It repeats the exact execution flaw described in the lessons learned.
            3. Market context matches the conditions that caused premature stopout.

            Output strictly a valid JSON object matching:
            {{
                "passed": true / false,
                "matching_failure_ticket": 12345 or null,
                "reasoning": "Clear explanation of why this trade passes or repeats an anti-pattern"
            }}
            """
            candidate_models = [self.model_name, "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest"]
            for model in candidate_models:
                try:
                    res = self.client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1
                        )
                    )
                    data = json.loads(res.text)
                    passed = bool(data.get("passed", True))
                    reasoning = data.get("reasoning", "AI self-check complete")
                    ticket = data.get("matching_failure_ticket")
                    if not passed:
                        return False, f"AI Pre-Trade Gate REJECTED: Repeats Failure #{ticket}: {reasoning}"
                    return True, f"AI Pre-Trade Gate PASSED: {reasoning}"
                except Exception as e:
                    logger.debug(f"Gemini pre-trade self check ({model}) failed: {e}")
                    continue

        # Heuristic fallback self-check
        for f in failures:
            # Check if same direction within $3.50 of failed entry
            if f["side"] == proposal.direction and f["entry_price"]:
                dist = abs(proposal.entry_price - float(f["entry_price"]))
                if dist < 3.50:
                    return False, f"Heuristic Anti-Pattern: Setup is within ${dist:.2f} of failed trade #{f['ticket']} ({proposal.direction} at {f['entry_price']})."

        return True, "Heuristic self-check passed: No repeating price levels or anti-patterns detected."

    def analyze_and_propose(
        self,
        d1_bars: List[Any],
        h4_bars: List[Any],
        h1_bars: List[Any],
        m30_bars: List[Any],
        m15_bars: List[Any],
        symbol: str = "XAUUSD"
    ) -> Optional[TradeProposal]:
        """Evaluates higher-timeframe confluence and emits a Swing Trade Proposal."""
        if len(h4_bars) < 20 or len(h1_bars) < 20 or len(m15_bars) < 20:
            return None

        # 1. Higher-Timeframe Flow (D1 & H4)
        h4_regime = SMCAnalyzer.calculate_macro_regime(h4_bars)
        h1_regime = SMCAnalyzer.calculate_macro_regime(h1_bars)

        # Macro Trend Direction
        h4_trend = h4_regime.get("trend", "NEUTRAL")
        h1_trend = h1_regime.get("trend", "NEUTRAL")

        # 2. Structural Confirmation (30m & 15m BOS / CHoCH)
        eval_bars = m30_bars if len(m30_bars) >= 20 else m15_bars
        bos_data = SMCAnalyzer.detect_bos_choch(eval_bars, left_bars=4, right_bars=4)
        atr_14 = SMCAnalyzer.calculate_atr(eval_bars, period=14)

        bos = bos_data.get("bos")
        choch = bos_data.get("choch")
        swing_highs = bos_data.get("swing_highs", [])
        swing_lows = bos_data.get("swing_lows", [])

        latest_close = float(eval_bars[-1]["close"] if isinstance(eval_bars[-1], dict) else eval_bars[-1].close)

        proposal = None
        confluences = []

        # === 3. BULLISH SWING SETUP ===
        is_bullish_structure = (bos and "BULLISH" in bos["type"]) or (choch and "BULLISH" in choch["type"])
        if is_bullish_structure and ("BULLISH" in h4_trend or "BULLISH" in h1_trend):
            # Macro Gate Check: Alpha Vantage US 10Y Yield Check
            allow_buy, macro_reason = self.macro_worker.check_swing_buy_permission()
            if not allow_buy:
                logger.warning(f"[Swing Trader] BUY Setup vetoed by Alpha Vantage Macro Gate: {macro_reason}")
                return None

            # Institutional Swing Stop Loss: Min $4.00, Max $8.00 - $10.00
            # If the nearest swing low is too far (e.g. > $8.00 due to parabolic momentum),
            # cap the stop behind the recent consolidation or ATR buffer to maintain realistic 1:1 R:R.
            max_swing_stop = max(8.00, 2.5 * atr_14)
            if swing_lows:
                anchor_low = swing_lows[-1]["price"]
                raw_sl = anchor_low - max(1.0, 0.5 * atr_14)
                if (latest_close - raw_sl) > max_swing_stop:
                    structural_sl = latest_close - max_swing_stop
                else:
                    structural_sl = raw_sl
            else:
                structural_sl = latest_close - max(4.0, 1.8 * atr_14)

            # Limit entry at 50% equilibrium or top edge of 30m/15m FVG or demand block
            fvgs = SMCAnalyzer.detect_fair_value_gaps(eval_bars, lookback=20)
            bullish_fvgs = [f for f in fvgs if f["type"] == "BULLISH_FVG" and f["gap_high"] <= latest_close]
            if bullish_fvgs:
                eq_entry = round((bullish_fvgs[-1]["gap_low"] + bullish_fvgs[-1]["gap_high"]) / 2.0, 2)
            else:
                eq_entry = round(latest_close - max(0.80, 0.4 * atr_14), 2)
            limit_entry = eq_entry if eq_entry < latest_close else round(latest_close - 0.50, 2)

            risk_dist = limit_entry - structural_sl
            if risk_dist > 0:
                target_rr = 3.0  # 1:3.0 institutional target
                suggested_tp = round(limit_entry + (risk_dist * target_rr), 2)

                confluences.append("H4/H1 Bullish Flow Alignment")
                confluences.append("30m/15m Bullish BOS/CHoCH Confirmed")
                confluences.append("Alpha Vantage 10Y Yield Check Passed")
                confluences.append("50% Equilibrium Limit Entry")

                proposal = TradeProposal(
                    proposal_id=f"SWING-{uuid.uuid4().hex[:6]}",
                    agent_role="SWING_TRADER",
                    timeframe="H1-M30",
                    symbol=symbol,
                    direction="BUY",
                    order_execution_type="LIMIT",
                    setup_cluster="SWING_M30_BOS_LIMIT",
                    ttl_minutes=20,
                    entry_price=limit_entry,
                    structural_sl=round(structural_sl, 2),
                    suggested_tp=suggested_tp,
                    confluence_score=80.0,
                    thesis=f"Institutional Swing Buy Limit: Aligned with H4/H1 trend, placed at 50% equilibrium ({limit_entry}) with {target_rr}:1 R:R target.",
                    supporting_confluences=confluences,
                    invalidation_condition=f"5m CHoCH or H1/30m close below structural swing low at {round(structural_sl, 2)}",
                    magic_number=self.MAGIC_NUMBER,
                    risk_pct=self.BASE_RISK_PCT
                )

        # === 4. BEARISH SWING SETUP ===
        is_bearish_structure = (bos and "BEARISH" in bos["type"]) or (choch and "BEARISH" in choch["type"])
        if not proposal and is_bearish_structure and ("BEARISH" in h4_trend or "BEARISH" in h1_trend):
            # Institutional Swing Stop Loss: Min $4.00, Max $8.00 - $10.00
            max_swing_stop = max(8.00, 2.5 * atr_14)
            if swing_highs:
                anchor_high = swing_highs[-1]["price"]
                raw_sl = anchor_high + max(1.0, 0.5 * atr_14)
                if (raw_sl - latest_close) > max_swing_stop:
                    structural_sl = latest_close + max_swing_stop
                else:
                    structural_sl = raw_sl
            else:
                structural_sl = latest_close + max(4.0, 1.8 * atr_14)

            # Ensure minimum $4.00 stop distance on Gold
            if (structural_sl - latest_close) < 4.0:
                structural_sl = latest_close + 4.50

            # Limit entry at 50% equilibrium or bottom edge of 30m/15m FVG or supply block
            fvgs = SMCAnalyzer.detect_fair_value_gaps(eval_bars, lookback=20)
            bearish_fvgs = [f for f in fvgs if f["type"] == "BEARISH_FVG" and f["gap_low"] >= latest_close]
            if bearish_fvgs:
                eq_entry = round((bearish_fvgs[-1]["gap_low"] + bearish_fvgs[-1]["gap_high"]) / 2.0, 2)
            else:
                eq_entry = round(latest_close + max(0.80, 0.4 * atr_14), 2)
            limit_entry = eq_entry if eq_entry > latest_close else round(latest_close + 0.50, 2)

            risk_dist = structural_sl - limit_entry
            if risk_dist > 0:
                target_rr = 3.0
                suggested_tp = round(limit_entry - (risk_dist * target_rr), 2)

                confluences.append("H4/H1 Bearish Flow Alignment")
                confluences.append("30m/15m Bearish BOS/CHoCH Confirmed")
                confluences.append("50% Equilibrium Limit Entry")

                proposal = TradeProposal(
                    proposal_id=f"SWING-{uuid.uuid4().hex[:6]}",
                    agent_role="SWING_TRADER",
                    timeframe="H1-M30",
                    symbol=symbol,
                    direction="SELL",
                    order_execution_type="LIMIT",
                    setup_cluster="SWING_M30_BOS_LIMIT",
                    ttl_minutes=20,
                    entry_price=limit_entry,
                    structural_sl=round(structural_sl, 2),
                    suggested_tp=suggested_tp,
                    confluence_score=80.0,
                    thesis=f"Institutional Swing Sell Limit: Aligned with H4/H1 trend, placed at 50% equilibrium ({limit_entry}) with {target_rr}:1 R:R target.",
                    supporting_confluences=confluences,
                    invalidation_condition=f"5m CHoCH or H1/30m close above structural swing high at {round(structural_sl, 2)}",
                    magic_number=self.MAGIC_NUMBER,
                    risk_pct=self.BASE_RISK_PCT
                )

        if proposal:
            past_failures = self._query_past_failures(limit=3)
            passed, self_check_msg = self._perform_pre_trade_self_check(proposal, past_failures)
            if not passed:
                logger.warning(f"[Swing Trader (1001)] PROPOSAL REJECTED BY PRE-TRADE POST-MORTEM GATE: {self_check_msg}")
                return None

            proposal.supporting_confluences.append(f"Post-Mortem Self-Check: {self_check_msg}")
            logger.info(
                f"[Swing Trader (1001)] PROPOSAL APPROVED: {proposal.direction} {symbol} at {proposal.entry_price} | "
                f"SL={proposal.structural_sl} (Dist: ${abs(proposal.entry_price - proposal.structural_sl):.2f}) | "
                f"TP={proposal.suggested_tp} | Risk={proposal.risk_pct*100:.1f}% | {self_check_msg}"
            )

        return proposal

    def evaluate_scale_in(
        self,
        m15_bars: List[Any],
        m5_bars: List[Any],
        h4_bars: List[Any],
        h1_bars: List[Any],
        symbol: str = "XAUUSD"
    ) -> Optional[TradeProposal]:
        """
        Confident Setup: Pullback Scale-In (Pyramiding)
        Prerequisites:
        1. Primary trade is running at >= 1:1 R:R.
        2. Primary trade Stop Loss is confirmed at Breakeven.
        3. Higher-timeframe structure remains firmly aligned.
        Entry Trigger:
        Price retraces into a 5m/15m Fair Value Gap or 50%–61.8% equilibrium zone and prints confirmation reversal candle.
        Sizing & Protection:
        Scale-In Order #2 sized at exactly 50% of the primary lot size, dedicated structural SL behind pullback low/high.
        """
        if not mt5.initialize():
            return None

        # Fetch active swing positions
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None

        swing_positions = [p for p in positions if p.magic == self.MAGIC_NUMBER]
        if not swing_positions:
            return None

        # Filter out existing scale-in positions
        primary_positions = [p for p in swing_positions if "ScaleIn" not in (p.comment or "")]
        scale_in_positions = [p for p in swing_positions if "ScaleIn" in (p.comment or "")]

        if not primary_positions or len(scale_in_positions) > 0:
            # Already scaled in or no primary position
            return None

        primary = primary_positions[0]
        side = "BUY" if primary.type == mt5.ORDER_TYPE_BUY else "SELL"
        entry_price = float(primary.price_open)
        current_price = float(primary.price_current)
        sl = float(primary.sl)
        volume = float(primary.volume)

        # 1. Prerequisite 2 Check: SL confirmed at Breakeven
        if sl <= 0:
            return None

        is_sl_at_be = False
        if side == "BUY" and sl >= (entry_price - 0.10):
            is_sl_at_be = True
        elif side == "SELL" and sl <= (entry_price + 0.10):
            is_sl_at_be = True

        if not is_sl_at_be:
            return None

        # 2. Prerequisite 1 Check: Running at >= 1:1 R:R
        # Estimate initial risk as at least $4.00 or distance from entry
        initial_risk_dist = max(4.0, abs(entry_price - sl) if sl > 0 else 4.0)
        current_profit_dist = (current_price - entry_price) if side == "BUY" else (entry_price - current_price)

        if current_profit_dist < initial_risk_dist:
            # Has not yet reached 1:1 R:R
            return None

        # 3. Prerequisite 3 Check: Higher-Timeframe Structure Firmly Aligned
        h4_regime = SMCAnalyzer.calculate_macro_regime(h4_bars) if h4_bars else {}
        h1_regime = SMCAnalyzer.calculate_macro_regime(h1_bars) if h1_bars else {}
        h4_trend = h4_regime.get("trend", "NEUTRAL")
        h1_trend = h1_regime.get("trend", "NEUTRAL")

        if side == "BUY" and ("BEARISH" in h4_trend or "BEARISH" in h1_trend):
            return None
        if side == "SELL" and ("BULLISH" in h4_trend or "BULLISH" in h1_trend):
            return None

        # 4. Entry Trigger Check: Retracement into 5m/15m FVG or 50%-61.8% equilibrium zone with reversal candle
        eval_bars = m5_bars if len(m5_bars) >= 15 else m15_bars
        if not eval_bars or len(eval_bars) < 10:
            return None

        fvgs = SMCAnalyzer.detect_fair_value_gaps(eval_bars, lookback=15)
        recent_high = max(float(b.get("high") if isinstance(b, dict) else b.high) for b in eval_bars[-15:])
        recent_low = min(float(b.get("low") if isinstance(b, dict) else b.low) for b in eval_bars[-15:])
        leg_range = recent_high - recent_low

        if leg_range <= 1.0:
            return None

        latest_bar = eval_bars[-1]
        l_open = float(latest_bar.get("open") if isinstance(latest_bar, dict) else latest_bar.open)
        l_close = float(latest_bar.get("close") if isinstance(latest_bar, dict) else latest_bar.close)
        l_high = float(latest_bar.get("high") if isinstance(latest_bar, dict) else latest_bar.high)
        l_low = float(latest_bar.get("low") if isinstance(latest_bar, dict) else latest_bar.low)

        scale_in_proposal = None
        scale_in_lot = round(volume * 0.5, 2)
        if scale_in_lot < 0.01:
            scale_in_lot = 0.01

        if side == "BUY":
            # Retracement into 50%-61.8% equilibrium zone [recent_low + 0.382*range, recent_low + 0.50*range]
            fib_50 = recent_high - (0.50 * leg_range)
            fib_618 = recent_high - (0.618 * leg_range)
            in_equilibrium = (fib_618 <= l_low <= fib_50) or (fib_618 <= current_price <= fib_50)

            # Or FVG mitigation
            in_fvg = False
            for f in fvgs:
                if f["type"] == "BULLISH_FVG" and f["gap_low"] <= l_low <= f["gap_high"]:
                    in_fvg = True
                    break

            # Reversal confirmation candle: Bullish candle with bottom rejection wick
            is_bullish_reversal = (l_close > l_open) and ((min(l_open, l_close) - l_low) >= 0.3 * (l_high - l_low))

            if (in_equilibrium or in_fvg) and is_bullish_reversal:
                pullback_sl = round(l_low - 0.80, 2)
                if (current_price - pullback_sl) < 2.0:
                    pullback_sl = round(current_price - 2.50, 2)

                target_tp = float(primary.tp) if primary.tp > 0 else round(current_price + (abs(current_price - pullback_sl) * 2.0), 2)

                scale_in_proposal = TradeProposal(
                    proposal_id=f"SWING-SCALEIN-{uuid.uuid4().hex[:6]}",
                    agent_role="SWING_SCALE_IN",
                    timeframe="M15-M5",
                    symbol=symbol,
                    direction="BUY",
                    entry_price=round(current_price, 2),
                    structural_sl=pullback_sl,
                    suggested_tp=target_tp,
                    confluence_score=85.0,
                    thesis=f"Pullback Scale-In #2: Primary #{primary.ticket} at BE. Confirmed retest of equilibrium/FVG zone.",
                    supporting_confluences=["Primary Trade Running >= 1:1 R:R with BE Locked", "5m/15m Pullback Reversal Confirmed"],
                    invalidation_condition=f"Close below pullback low at {pullback_sl}",
                    magic_number=self.MAGIC_NUMBER,
                    risk_pct=0.0075,
                    is_scale_in=True,
                    parent_ticket=primary.ticket
                )

        elif side == "SELL":
            fib_50 = recent_low + (0.50 * leg_range)
            fib_618 = recent_low + (0.618 * leg_range)
            in_equilibrium = (fib_50 <= l_high <= fib_618) or (fib_50 <= current_price <= fib_618)

            in_fvg = False
            for f in fvgs:
                if f["type"] == "BEARISH_FVG" and f["gap_low"] <= l_high <= f["gap_high"]:
                    in_fvg = True
                    break

            # Reversal confirmation candle: Bearish candle with top rejection wick
            is_bearish_reversal = (l_close < l_open) and ((l_high - max(l_open, l_close)) >= 0.3 * (l_high - l_low))

            if (in_equilibrium or in_fvg) and is_bearish_reversal:
                pullback_sl = round(l_high + 0.80, 2)
                if (pullback_sl - current_price) < 2.0:
                    pullback_sl = round(current_price + 2.50, 2)

                target_tp = float(primary.tp) if primary.tp > 0 else round(current_price - (abs(pullback_sl - current_price) * 2.0), 2)

                scale_in_proposal = TradeProposal(
                    proposal_id=f"SWING-SCALEIN-{uuid.uuid4().hex[:6]}",
                    agent_role="SWING_SCALE_IN",
                    timeframe="M15-M5",
                    symbol=symbol,
                    direction="SELL",
                    entry_price=round(current_price, 2),
                    structural_sl=pullback_sl,
                    suggested_tp=target_tp,
                    confluence_score=85.0,
                    thesis=f"Pullback Scale-In #2: Primary #{primary.ticket} at BE. Confirmed retest of equilibrium/FVG zone.",
                    supporting_confluences=["Primary Trade Running >= 1:1 R:R with BE Locked", "5m/15m Pullback Reversal Confirmed"],
                    invalidation_condition=f"Close above pullback high at {pullback_sl}",
                    magic_number=self.MAGIC_NUMBER,
                    risk_pct=0.0075,
                    is_scale_in=True,
                    parent_ticket=primary.ticket
                )

        if scale_in_proposal:
            logger.info(
                f"[Swing Trader (1001)] SCALE-IN TRIGGERED for #{primary.ticket}! "
                f"Direction={scale_in_proposal.direction} at {scale_in_proposal.entry_price} | "
                f"SL={scale_in_proposal.structural_sl} | TP={scale_in_proposal.suggested_tp}"
            )

        return scale_in_proposal

