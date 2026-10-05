import logging
import uuid
from typing import List, Dict, Any, Optional
from app.indicators.smc import SMCAnalyzer
from app.agents.trade_proposal import TradeProposal

logger = logging.getLogger(__name__)

class StructuralScout:
    """
    Structural Scout (M15 Timeframe):
    - Scans for Break of Structure (BOS) and Change of Character (CHoCH)
    - Ensures alignment with the Macro Director's HTF regime
    - Calculates institutional entries with structural swing invalidations
    - Enforces Target Risk-to-Reward ratio: 1:2.5 to 1:4.0
    - Emits formal TradeProposal (NEVER executes trades directly)
    """

    MIN_RR = 2.5
    MAX_RR = 4.0

    def __init__(self):
        pass

    def scan_structure(
        self,
        m15_bars: List[Any],
        macro_regime: Dict[str, Any],
        symbol: str = "XAUUSD"
    ) -> Optional[TradeProposal]:
        """
        Scans M15 market structure and emits a trade proposal if a high-confluence setup exists.
        """
        if len(m15_bars) < 20:
            return None

        bos_data = SMCAnalyzer.detect_bos_choch(m15_bars, left_bars=3, right_bars=3)
        atr_14 = SMCAnalyzer.calculate_atr(m15_bars, period=14)
        latest_close = float(m15_bars[-1]["close"] if isinstance(m15_bars[-1], dict) else m15_bars[-1].close)

        bos = bos_data.get("bos")
        choch = bos_data.get("choch")
        swing_highs = bos_data.get("swing_highs", [])
        swing_lows = bos_data.get("swing_lows", [])

        proposal = None
        confluences = []

        # 1. BULLISH SETUP: Align with HTF Bullish/Neutral regime
        if macro_regime.get("allow_long", True):
            is_bullish_bos = bos and "BULLISH" in bos["type"]
            is_bullish_choch = choch and "BULLISH" in choch["type"]

            if is_bullish_bos or is_bullish_choch:
                if swing_lows:
                    structural_sl = swing_lows[-1]["price"] - (0.5 * atr_14)
                else:
                    structural_sl = latest_close - (1.5 * atr_14)

                risk_distance = latest_close - structural_sl
                if risk_distance > 0:
                    target_rr = 3.0
                    suggested_tp = latest_close + (risk_distance * target_rr)

                    confluences.append("M15 Bullish Structure (" + ("BOS" if is_bullish_bos else "CHoCH") + ")")
                    if macro_regime.get("regime") == "BULLISH":
                        confluences.append("Aligned with H4/H1 Bullish Macro Regime")
                    if latest_close > macro_regime.get("key_support", 0):
                        confluences.append("Trading above Macro Key Support")

                    confluence_score = 65.0 + (15.0 if macro_regime.get("regime") == "BULLISH" else 0.0) + (10.0 if len(confluences) >= 2 else 0.0)

                    proposal = TradeProposal(
                        proposal_id=f"SCOUT-M15-{uuid.uuid4().hex[:6]}",
                        agent_role="STRUCTURAL_SCOUT",
                        timeframe="M15",
                        symbol=symbol,
                        direction="BUY",
                        entry_price=round(latest_close, 2),
                        structural_sl=round(structural_sl, 2),
                        suggested_tp=round(suggested_tp, 2),
                        confluence_score=round(confluence_score, 1),
                        thesis=f"M15 Structural Long: {confluences[0]} confirmed with {target_rr}:1 R:R target.",
                        supporting_confluences=confluences,
                        invalidation_condition=f"M15 close below structural swing low at {round(structural_sl, 2)}"
                    )

        # 2. BEARISH SETUP: Align with HTF Bearish/Neutral regime
        if not proposal and macro_regime.get("allow_short", True):
            is_bearish_bos = bos and "BEARISH" in bos["type"]
            is_bearish_choch = choch and "BEARISH" in choch["type"]

            if is_bearish_bos or is_bearish_choch:
                if swing_highs:
                    structural_sl = swing_highs[-1]["price"] + (0.5 * atr_14)
                else:
                    structural_sl = latest_close + (1.5 * atr_14)

                risk_distance = structural_sl - latest_close
                if risk_distance > 0:
                    target_rr = 3.0
                    suggested_tp = latest_close - (risk_distance * target_rr)

                    confluences.append("M15 Bearish Structure (" + ("BOS" if is_bearish_bos else "CHoCH") + ")")
                    if macro_regime.get("regime") == "BEARISH":
                        confluences.append("Aligned with H4/H1 Bearish Macro Regime")
                    if latest_close < macro_regime.get("key_resistance", float("inf")):
                        confluences.append("Trading below Macro Key Resistance")

                    confluence_score = 65.0 + (15.0 if macro_regime.get("regime") == "BEARISH" else 0.0) + (10.0 if len(confluences) >= 2 else 0.0)

                    proposal = TradeProposal(
                        proposal_id=f"SCOUT-M15-{uuid.uuid4().hex[:6]}",
                        agent_role="STRUCTURAL_SCOUT",
                        timeframe="M15",
                        symbol=symbol,
                        direction="SELL",
                        entry_price=round(latest_close, 2),
                        structural_sl=round(structural_sl, 2),
                        suggested_tp=round(suggested_tp, 2),
                        confluence_score=round(confluence_score, 1),
                        thesis=f"M15 Structural Short: {confluences[0]} confirmed with {target_rr}:1 R:R target.",
                        supporting_confluences=confluences,
                        invalidation_condition=f"M15 close above structural swing high at {round(structural_sl, 2)}"
                    )

        if proposal:
            logger.info(
                f"[Structural Scout] Generated {proposal.direction} Proposal: "
                f"Entry={proposal.entry_price}, SL={proposal.structural_sl}, TP={proposal.suggested_tp}, "
                f"R:R={proposal.risk_reward_ratio}, Confluence={proposal.confluence_score}"
            )

        return proposal
