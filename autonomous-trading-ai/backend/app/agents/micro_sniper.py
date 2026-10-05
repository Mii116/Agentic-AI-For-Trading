import logging
import uuid
from typing import List, Dict, Any, Optional
from app.indicators.smc import SMCAnalyzer
from app.agents.trade_proposal import TradeProposal

logger = logging.getLogger(__name__)

class MicroSniper:
    """
    Micro-Scalp Sniper (M5 / M1 Timeframes):
    - Scans for session liquidity sweeps (Asian / London extremes)
    - Detects Fair Value Gaps (FVG) and Market Structure Shifts (MSS)
    - Sets structural Stop Loss strictly behind the liquidity sweep wick extreme
    - Enforces Target Risk-to-Reward ratio: 1:1.5 to 1:2.0
    - Emits formal TradeProposal (NEVER executes trades directly)
    """

    MIN_RR = 1.5
    MAX_RR = 2.0

    def __init__(self):
        pass

    def scan_micro_liquidity(
        self,
        m5_bars: List[Any],
        m1_bars: List[Any],
        macro_regime: Dict[str, Any],
        symbol: str = "XAUUSD"
    ) -> Optional[TradeProposal]:
        """
        Scans M5/M1 for liquidity sweep setups and Fair Value Gap entries.
        """
        if len(m5_bars) < 15:
            return None

        sweeps = SMCAnalyzer.detect_session_liquidity_sweeps(m5_bars)
        fvgs = SMCAnalyzer.detect_fair_value_gaps(m5_bars, lookback=20)
        atr_14 = SMCAnalyzer.calculate_atr(m5_bars, period=14)

        latest_close = float(m5_bars[-1]["close"] if isinstance(m5_bars[-1], dict) else m5_bars[-1].close)

        asian_sweep = sweeps.get("asian_sweep")
        london_sweep = sweeps.get("london_sweep")
        active_sweep = london_sweep or asian_sweep

        proposal = None
        confluences = []

        # 1. BULLISH SWEEP REVERSAL (Swept liquidity below Asian/London Low)
        if active_sweep and "BULLISH" in active_sweep["type"] and macro_regime.get("allow_long", True):
            sweep_wick = active_sweep["sweep_wick_extreme"]
            # Structural invalidation behind the sweep wick
            structural_sl = round(sweep_wick - 0.30, 2)
            risk_dist = latest_close - structural_sl

            if risk_dist > 0:
                target_rr = 1.8
                suggested_tp = round(latest_close + (risk_dist * target_rr), 2)

                confluences.append(f"Session Low Liquidity Sweep ({active_sweep['type']})")
                
                # Check for supportive Bullish FVG
                bullish_fvgs = [f for f in fvgs if f["type"] == "BULLISH_FVG"]
                if bullish_fvgs:
                    confluences.append("Bullish FVG confluence on M5")

                if macro_regime.get("regime") in ["BULLISH", "NEUTRAL"]:
                    confluences.append("Aligned with HTF Macro Trend")

                confluence_score = 70.0 + (10.0 if bullish_fvgs else 0.0) + (10.0 if macro_regime.get("regime") == "BULLISH" else 0.0)

                proposal = TradeProposal(
                    proposal_id=f"SNIPER-M5-{uuid.uuid4().hex[:6]}",
                    agent_role="MICRO_SNIPER",
                    timeframe="M5",
                    symbol=symbol,
                    direction="BUY",
                    entry_price=round(latest_close, 2),
                    structural_sl=structural_sl,
                    suggested_tp=suggested_tp,
                    confluence_score=round(confluence_score, 1),
                    thesis=f"Micro Scalp Long: Swept session low at {active_sweep['swept_level']} with rejection wick at {sweep_wick}.",
                    supporting_confluences=confluences,
                    invalidation_condition=f"Breach of sweep wick extreme at {structural_sl}"
                )

        # 2. BEARISH SWEEP REVERSAL (Swept liquidity above Asian/London High)
        elif active_sweep and "BEARISH" in active_sweep["type"] and macro_regime.get("allow_short", True):
            sweep_wick = active_sweep["sweep_wick_extreme"]
            # Structural invalidation behind the sweep wick
            structural_sl = round(sweep_wick + 0.30, 2)
            risk_dist = structural_sl - latest_close

            if risk_dist > 0:
                target_rr = 1.8
                suggested_tp = round(latest_close - (risk_dist * target_rr), 2)

                confluences.append(f"Session High Liquidity Sweep ({active_sweep['type']})")

                # Check for supportive Bearish FVG
                bearish_fvgs = [f for f in fvgs if f["type"] == "BEARISH_FVG"]
                if bearish_fvgs:
                    confluences.append("Bearish FVG confluence on M5")

                if macro_regime.get("regime") in ["BEARISH", "NEUTRAL"]:
                    confluences.append("Aligned with HTF Macro Trend")

                confluence_score = 70.0 + (10.0 if bearish_fvgs else 0.0) + (10.0 if macro_regime.get("regime") == "BEARISH" else 0.0)

                proposal = TradeProposal(
                    proposal_id=f"SNIPER-M5-{uuid.uuid4().hex[:6]}",
                    agent_role="MICRO_SNIPER",
                    timeframe="M5",
                    symbol=symbol,
                    direction="SELL",
                    entry_price=round(latest_close, 2),
                    structural_sl=structural_sl,
                    suggested_tp=suggested_tp,
                    confluence_score=round(confluence_score, 1),
                    thesis=f"Micro Scalp Short: Swept session high at {active_sweep['swept_level']} with rejection wick at {sweep_wick}.",
                    supporting_confluences=confluences,
                    invalidation_condition=f"Breach of sweep wick extreme at {structural_sl}"
                )

        # 3. FAIR VALUE GAP MITIGATION ENTRY (If no sweep active, scan for unmitigated FVG)
        elif not proposal and fvgs:
            latest_fvg = fvgs[-1]
            if latest_fvg["type"] == "BULLISH_FVG" and macro_regime.get("allow_long", True):
                structural_sl = round(latest_fvg["gap_low"] - (0.5 * atr_14), 2)
                risk_dist = latest_close - structural_sl
                if risk_dist > 0:
                    suggested_tp = round(latest_close + (risk_dist * 1.8), 2)
                    confluences.append(f"M5 Bullish Fair Value Gap [{latest_fvg['gap_low']} - {latest_fvg['gap_high']}]")
                    confluence_score = 65.0
                    proposal = TradeProposal(
                        proposal_id=f"SNIPER-FVG-{uuid.uuid4().hex[:6]}",
                        agent_role="MICRO_SNIPER",
                        timeframe="M5",
                        symbol=symbol,
                        direction="BUY",
                        entry_price=round(latest_close, 2),
                        structural_sl=structural_sl,
                        suggested_tp=suggested_tp,
                        confluence_score=confluence_score,
                        thesis="M5 FVG Scalp Long at unmitigated bullish imbalance.",
                        supporting_confluences=confluences,
                        invalidation_condition=f"Close below FVG lower boundary {structural_sl}"
                    )
            elif latest_fvg["type"] == "BEARISH_FVG" and macro_regime.get("allow_short", True):
                structural_sl = round(latest_fvg["gap_high"] + (0.5 * atr_14), 2)
                risk_dist = structural_sl - latest_close
                if risk_dist > 0:
                    suggested_tp = round(latest_close - (risk_dist * 1.8), 2)
                    confluences.append(f"M5 Bearish Fair Value Gap [{latest_fvg['gap_low']} - {latest_fvg['gap_high']}]")
                    confluence_score = 65.0
                    proposal = TradeProposal(
                        proposal_id=f"SNIPER-FVG-{uuid.uuid4().hex[:6]}",
                        agent_role="MICRO_SNIPER",
                        timeframe="M5",
                        symbol=symbol,
                        direction="SELL",
                        entry_price=round(latest_close, 2),
                        structural_sl=structural_sl,
                        suggested_tp=suggested_tp,
                        confluence_score=confluence_score,
                        thesis="M5 FVG Scalp Short at unmitigated bearish imbalance.",
                        supporting_confluences=confluences,
                        invalidation_condition=f"Close above FVG upper boundary {structural_sl}"
                    )

        if proposal:
            logger.info(
                f"[Micro Sniper] Generated {proposal.direction} Scalp Proposal: "
                f"Entry={proposal.entry_price}, SL={proposal.structural_sl}, TP={proposal.suggested_tp}, "
                f"R:R={proposal.risk_reward_ratio}, Confluence={proposal.confluence_score}"
            )

        return proposal
