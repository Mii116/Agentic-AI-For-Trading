import logging
import time
import uuid
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

import MetaTrader5 as mt5

from app.db.session import SessionLocal
from app.models.trading import TradeJournal
from app.agents.trade_proposal import TradeProposal
from app.indicators import scalp_signals as sig
from app.risk.stop_policy import scalp_stop_distance

logger = logging.getLogger(__name__)


class ScalpTrader:
    """
    Scalp Trader ("The Liquid Sniper") — Magic: 2002
    - 100% DETERMINISTIC: no LLM / network calls anywhere in this class. Setup detection is
      pure vectorized numpy on CLOSED M5 bars and completes in well under 1 ms.
    - Timeframe Stack: M5 structure; tick-level confirmation is handled by ZoneRetestMonitor.
    - Strategy & Objective: Liquidity sweeps of PDH/PDL, Asian and London ranges, and FRESH
      (untested) institutional Fair Value Gaps.
    - Entry Model: ZONE_RETEST (no passive 50% limits). The trader only *arms* a zone; the
      monitor fires a market order when price mitigates the zone AND prints a displacement
      confirmation tick. Price trading through the zone disarms it (adverse-selection filter).
    - Stop Model: SL distance = max(structural, 1.5 x ATR(14, M5), $2.00) — never < $1.50.
    - Hedging Independence: Completely exempt from the Swing Trader's bias.
    - Risk Allocation: 0.25% to 0.50% equity risk per trade
    - Execution Windows (Session Filter): London & NY volatility windows (07:00–17:00 UTC).
    - Macro Exemption: Completely exempt from Alpha Vantage macro filter.
    - Post-Mortem Pre-Trade Gate: deterministic price-zone anti-pattern check against the last
      3 failed trades for Magic 2002 (cached, no LLM).
    """

    MAGIC_NUMBER = 2002
    BASE_RISK_PCT = 0.005  # 0.50% risk per scalp trade

    M5_HISTORY_BARS = 400      # ~33h of M5 so PDH/PDL and the Asian range are always in view
    TARGET_RR = 2.0            # TP multiple of the (volatility-adjusted) risk
    MAX_STRUCT_ATR = 3.5       # Skip zones whose structural stop exceeds 3.5 x ATR ...
    MAX_STRUCT_ABS = 6.00      # ... and $6.00 (poor R:R / tiny size)
    MAX_ARM_DISTANCE_ATR = 4.0 # Don't arm zones price is unlikely to revisit within TTL
    ZONE_TTL_MIN = 20
    FAILURE_CACHE_SEC = 60.0

    def __init__(self):
        self._failure_cache: List[Dict[str, Any]] = []
        self._failure_cache_at: float = 0.0
        self._recent_zone_keys: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Post-mortem anti-pattern gate (deterministic)
    # ------------------------------------------------------------------
    def _query_past_failures(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Queries the trade_journals table for the last 3 failed trades for Magic 2002 (cached 60s)."""
        now = time.time()
        if now - self._failure_cache_at < self.FAILURE_CACHE_SEC:
            return self._failure_cache

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
                    "why_it_went_wrong": f.why_it_went_wrong or "Micro-structure broke down before expansion.",
                    "lessons_learned": f.lessons_learned or "Wait for confirmed rejection and avoid choppy liquidity pools.",
                    "danger_trigger": f.danger_trigger or "N/A",
                    "technique_used": f.technique_used
                })
            self._failure_cache = results
            self._failure_cache_at = now
            return results
        except Exception as e:
            logger.error(f"Error querying past failures for Scalp Trader: {e}")
            return self._failure_cache
        finally:
            db.close()

    def _perform_pre_trade_self_check(self, proposal: TradeProposal, failures: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Explicit Pre-Trade Gate (deterministic):
        Rejects a scalp whose zone sits within $3.00 of a recent failed entry in the same direction.
        """
        if not failures:
            return True, "No recent failed trades on record for Scalp Trader (2002)."

        zl = proposal.zone_low if proposal.zone_low is not None else proposal.entry_price
        zh = proposal.zone_high if proposal.zone_high is not None else proposal.entry_price
        for f in failures:
            if f["side"] == proposal.direction and f["entry_price"]:
                fp = float(f["entry_price"])
                # Distance from the failed entry to the nearest point of the zone
                dist = 0.0 if zl <= fp <= zh else min(abs(fp - zl), abs(fp - zh))
                if dist < 3.00:
                    return False, (
                        f"Anti-Pattern: zone [{zl}-{zh}] is within ${dist:.2f} of failed trade "
                        f"#{f['ticket']} ({proposal.direction} at {fp})."
                    )
        return True, "Deterministic self-check passed: no repeating price zones."

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    def is_volatility_window(self) -> bool:
        """
        Session Filter:
        Only allow new scalper entries during London & New York volatility windows (07:00 - 17:00 UTC).
        Blocks entries during low-liquidity Asian/off-hours ranges to prevent spread drag.
        """
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        # 07:00 UTC to 17:00 UTC
        return 7 <= current_hour < 17

    def _is_duplicate_zone(self, key: str) -> bool:
        now = time.time()
        self._recent_zone_keys = {k: v for k, v in self._recent_zone_keys.items() if v > now}
        if key in self._recent_zone_keys:
            return True
        self._recent_zone_keys[key] = now + self.ZONE_TTL_MIN * 60
        return False

    # ------------------------------------------------------------------
    # Proposal construction
    # ------------------------------------------------------------------
    def _build_zone_proposal(
        self,
        symbol: str,
        direction: str,
        zone_low: float,
        zone_high: float,
        structural_anchor: float,
        invalidation: float,
        atr_m5: float,
        cluster: str,
        score: float,
        thesis_core: str,
        confluences: List[str],
        live_price: float,
    ) -> Optional[TradeProposal]:
        """Converts a zone into a volatility-sized ZONE_RETEST proposal (or None if not tradable)."""
        buf = max(0.30, 0.30 * atr_m5)
        if direction == "BUY":
            ref_entry = zone_high
            structural_sl_raw = structural_anchor - buf
            structural_dist = ref_entry - structural_sl_raw
            if live_price < invalidation:
                return None  # zone already traded through
            if live_price - zone_high > self.MAX_ARM_DISTANCE_ATR * atr_m5:
                return None
        else:
            ref_entry = zone_low
            structural_sl_raw = structural_anchor + buf
            structural_dist = structural_sl_raw - ref_entry
            if live_price > invalidation:
                return None
            if zone_low - live_price > self.MAX_ARM_DISTANCE_ATR * atr_m5:
                return None

        if structural_dist <= 0 or structural_dist > max(self.MAX_STRUCT_ATR * atr_m5, self.MAX_STRUCT_ABS):
            logger.debug(f"[Scalp Trader] Skip {cluster} {direction}: structural stop ${structural_dist:.2f} too wide/invalid.")
            return None

        dist = scalp_stop_distance(structural_dist, atr_m5)
        if direction == "BUY":
            sl = round(ref_entry - dist, 2)
            tp = round(ref_entry + self.TARGET_RR * dist, 2)
        else:
            sl = round(ref_entry + dist, 2)
            tp = round(ref_entry - self.TARGET_RR * dist, 2)

        key = f"{direction}|{round(zone_low, 1)}|{round(zone_high, 1)}"
        if self._is_duplicate_zone(key):
            return None

        confluences = list(confluences) + [
            "London/NY Volatility Window Active",
            f"Vol-Adjusted SL ${dist:.2f} = max(struct ${structural_dist:.2f}, 1.5xATR ${1.5 * atr_m5:.2f}, $2.00)",
            "Entry: tick-confirmed zone retest (no passive limit)",
        ]
        side_word = "below" if direction == "BUY" else "above"
        return TradeProposal(
            proposal_id=f"SCALP-{cluster.split('_')[1]}-{uuid.uuid4().hex[:6]}",
            agent_role="SCALP_TRADER",
            timeframe="M5-TICK",
            symbol=symbol,
            direction=direction,
            order_execution_type="ZONE_RETEST",
            setup_cluster=cluster,
            ttl_minutes=self.ZONE_TTL_MIN,
            entry_price=round(ref_entry, 2),
            structural_sl=sl,
            suggested_tp=tp,
            confluence_score=score,
            thesis=(
                f"{thesis_core} Armed zone [{zone_low:.2f}-{zone_high:.2f}]; fire on displacement tick. "
                f"SL {sl} (${dist:.2f}), TP 1:{self.TARGET_RR} from fill."
            ),
            supporting_confluences=confluences,
            invalidation_condition=f"Price trades {side_word} {invalidation:.2f} before confirmation, or TTL {self.ZONE_TTL_MIN}m",
            magic_number=self.MAGIC_NUMBER,
            risk_pct=self.BASE_RISK_PCT,
            zone_low=round(zone_low, 2),
            zone_high=round(zone_high, 2),
            invalidation_price=round(invalidation, 2),
            atr_m5=atr_m5,
            target_rr=self.TARGET_RR,
        )

    def analyze_and_propose(
        self,
        m5_bars: List[Any],
        m1_bars: List[Any],
        symbol: str = "XAUUSD"
    ) -> Optional[TradeProposal]:
        """
        Deterministic scan of CLOSED M5 bars for session-liquidity sweeps and fresh FVGs.
        Returns a ZONE_RETEST proposal (armed by the Arbiter into ZoneRetestMonitor) or None.
        `m5_bars`/`m1_bars` are only used as a fallback when MT5 rates are unavailable.
        """
        t0 = time.perf_counter()

        # 1. Session Volatility Filter Check
        if not self.is_volatility_window():
            now_hour = datetime.now(timezone.utc).hour
            logger.debug(f"[Scalp Trader] Outside London/NY window ({now_hour}:00 UTC). Off-hours scalp filter active.")
            return None

        # 2. Closed-bar data with true-UTC timestamps
        arr = sig.fetch_closed_rates(symbol, mt5.TIMEFRAME_M5, self.M5_HISTORY_BARS)
        utc_reliable = arr is not None
        if arr is None:
            arr = sig.bars_to_arrays(m5_bars, drop_last=True)
        if arr is None or len(arr["close"]) < 30:
            return None

        atr_m5 = sig.atr(arr, 14)
        tick = mt5.symbol_info_tick(symbol)
        live_price = float(tick.bid) if tick and tick.bid > 0 else float(arr["close"][-1])

        proposal: Optional[TradeProposal] = None

        # === 3. SESSION LIQUIDITY SWEEP REVERSALS (requires correct UTC sessions) ===
        if utc_reliable:
            levels = sig.session_levels(arr)
            sweep = sig.detect_sweep(arr, levels, atr_m5, lookback=3)
            if sweep:
                lvl, ext = sweep["level"], sweep["extreme"]
                min_zone = 0.30 * atr_m5
                if sweep["direction"] == "BUY":
                    zl, zh = ext, max(lvl, ext + min_zone)
                    invalidation = round(ext - 0.10, 2)
                else:
                    zl, zh = min(lvl, ext - min_zone), ext
                    invalidation = round(ext + 0.10, 2)
                proposal = self._build_zone_proposal(
                    symbol=symbol, direction=sweep["direction"], zone_low=zl, zone_high=zh,
                    structural_anchor=ext, invalidation=invalidation, atr_m5=atr_m5,
                    cluster="M5_SWEEP_RETEST", score=75.0,
                    thesis_core=f"{sweep['level_name']} liquidity sweep at {lvl} (wick {ext}).",
                    confluences=[f"Closed-bar {sweep['level_name']} sweep ({sweep['direction']})"],
                    live_price=live_price,
                )

        # === 4. FRESH FAIR VALUE GAP MITIGATION ===
        if not proposal:
            fvgs = sig.detect_fresh_fvgs(arr, atr_m5, lookback=30)
            for fvg in reversed(fvgs):  # newest first
                gl, gh = fvg["gap_low"], fvg["gap_high"]
                if fvg["direction"] == "BUY":
                    anchor, invalidation = gl, round(gl - 0.25 * atr_m5, 2)
                else:
                    anchor, invalidation = gh, round(gh + 0.25 * atr_m5, 2)
                proposal = self._build_zone_proposal(
                    symbol=symbol, direction=fvg["direction"], zone_low=gl, zone_high=gh,
                    structural_anchor=anchor, invalidation=invalidation, atr_m5=atr_m5,
                    cluster="M5_FVG_RETEST", score=70.0,
                    thesis_core=f"Fresh M5 {'Bullish' if fvg['direction'] == 'BUY' else 'Bearish'} FVG (displacement >= 0.8xATR).",
                    confluences=[f"Fresh M5 FVG [{gl} - {gh}] (first touch)"],
                    live_price=live_price,
                )
                if proposal:
                    break

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if proposal:
            past_failures = self._query_past_failures(limit=3)
            passed, self_check_msg = self._perform_pre_trade_self_check(proposal, past_failures)
            if not passed:
                logger.warning(f"[Scalp Trader (2002)] PROPOSAL REJECTED BY PRE-TRADE POST-MORTEM GATE: {self_check_msg}")
                return None

            proposal.supporting_confluences.append(f"Post-Mortem Self-Check: {self_check_msg}")
            logger.info(
                f"[Scalp Trader (2002)] ZONE ARMED-CANDIDATE: {proposal.direction} {symbol} zone "
                f"[{proposal.zone_low}-{proposal.zone_high}] | SL={proposal.structural_sl} "
                f"(Dist: ${abs(proposal.entry_price - proposal.structural_sl):.2f}, ATR ${atr_m5:.2f}) | "
                f"Risk={proposal.risk_pct*100:.2f}% | scan {elapsed_ms:.1f} ms"
            )
        else:
            logger.debug(f"[Scalp Trader (2002)] No setup (scan {elapsed_ms:.1f} ms, ATR ${atr_m5:.2f}).")

        return proposal
