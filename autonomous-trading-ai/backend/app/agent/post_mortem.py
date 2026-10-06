import logging
import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from google import genai
from google.genai import types

from app.config.settings import settings
from app.db.session import SessionLocal
from app.models.trading import TradeJournal

logger = logging.getLogger(__name__)

class PostMortemEngine:
    """
    Automated AI Post-Mortem & Self-Reflection Engine:
    - Analyzes every closed trade (especially losses & emergency closes)
    - Diagnoses why the entry went wrong and what market dynamics failed the thesis
    - Extracts concrete lessons learned and stores them in TradeJournal
    - Feeds lessons learned back into Strategy Agents to prevent repeating mistakes
    """

    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def analyze_closed_trade(
        self,
        journal_id: int,
        entry_bars_summary: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """
        Runs dual-sided continuous AI learning on 100% of completed trades in TradeJournal.
        - On WIN: Formulates a 'Winning Setup Signature' and records contributing factors.
        - On LOSS: Formulates a 'Failure Anti-Pattern' and diagnoses failure mode.
        - Automatically feeds experience to ExperienceEngine (experience_matrix.json).
        """
        from app.agent.experience_engine import ExperienceEngine
        exp_engine = ExperienceEngine()

        db = SessionLocal()
        try:
            trade = db.query(TradeJournal).filter_by(id=journal_id).first()
            if not trade:
                logger.warning(f"TradeJournal #{journal_id} not found for post-mortem.")
                return None

            is_win = (trade.realized_pnl is not None and trade.realized_pnl > 0)
            trade.status = "PASS" if is_win else ("FAIL" if (trade.realized_pnl or 0) < -5.0 else "BREAKEVEN")

            diagnosis = self._generate_ai_critique(trade, entry_bars_summary, is_win=is_win)

            if is_win:
                signature = diagnosis.get("winning_signature", "Limit entry executed at key liquidity pool with clean momentum expansion.")
                factor = diagnosis.get("contributing_factor", "Accurate limit placement and session volume confirmation.")
                trade.why_it_went_wrong = f"WINNING TRADE: {factor}"
                trade.lessons_learned = f"Winning Setup Signature: {signature}"

                exp_engine.record_trade_outcome(
                    setup_type=trade.technique_used,
                    is_win=True,
                    signature_or_anti_pattern=signature,
                    ticket=trade.ticket
                )
            else:
                anti_pattern = diagnosis.get("failure_anti_pattern", diagnosis.get("lessons_learned", "Avoid trading into compression or spread expansion."))
                why = diagnosis.get("why_it_went_wrong", "Structural invalidation breached due to adverse market shift.")
                trade.why_it_went_wrong = why
                trade.lessons_learned = f"Failure Anti-Pattern: {anti_pattern}"

                exp_engine.record_trade_outcome(
                    setup_type=trade.technique_used,
                    is_win=False,
                    signature_or_anti_pattern=anti_pattern,
                    ticket=trade.ticket
                )

            db.commit()

            logger.info(
                f"[Post-Mortem Engine] Dual-Sided Reflection for #{trade.ticket or trade.id}: "
                f"Outcome={trade.status} (${trade.realized_pnl:.2f}) | "
                f"{'Signature' if is_win else 'Anti-Pattern'}: {trade.lessons_learned[:90]}..."
            )
            return {"why_it_went_wrong": trade.why_it_went_wrong, "lessons_learned": trade.lessons_learned}

        except Exception as e:
            db.rollback()
            logger.error(f"Error during dual-sided post-mortem analysis: {e}")
            return None
        finally:
            db.close()

    def _generate_ai_critique(self, trade: TradeJournal, bars_summary: Optional[str], is_win: bool = False) -> Dict[str, str]:
        """Queries Gemini for dual-sided reflection with specialized trader analysis, or executes instant local heuristic."""
        if not settings.ENABLE_CLOUD_AI or not self.client:
            return self._heuristic_fallback(trade, is_win=is_win)

        magic = trade.magic_number or 1001
        is_scalper = (magic == 2002)

        if is_scalper:
            agent_profile = "SCALP TRADER (The Liquid Sniper - Magic 2002, 5m/1m micro price action)"
        else:
            agent_profile = "SWING TRADER (The Institutional Partner - Magic 1001, H4/H1/30m structural flow)"

        if is_win:
            prompt = f"""
            You are the Chief Quantitative Risk Auditor reviewing a WINNING closed trade on {trade.symbol}.
            Trader Profile: {agent_profile}
            Magic Number: {magic}

            === WINNING TRADE DETAILS ===
            - Ticket: {trade.ticket}
            - Side: {trade.side}
            - Volume / Lot Size: {trade.lot_size} lots
            - Entry Price: {trade.entry_price} | Exit Price: {trade.exit_price}
            - Stop Loss: {trade.stop_loss} | Take Profit: {trade.take_profit}
            - Realized Profit: +${trade.realized_pnl:.2f}
            - Technique Used: {trade.technique_used}
            - Original Entry Thesis: {trade.reason}

            === WINNING ANALYSIS DIRECTIVE ===
            Analyze why this XAUUSD trade won. Identify the primary contributing factor: limit placement accuracy, session volume, higher-timeframe confluence, or risk-to-reward ratio. Formulate a 'Winning Setup Signature'.

            Output strictly a valid JSON object matching:
            {{
                "contributing_factor": "Primary contributing factor behind this victory (e.g. precise limit fill at 50% FVG during London open)",
                "winning_signature": "Concise 1-2 sentence repeatable setup signature rule to reinforce on future limit setups"
            }}
            """
        else:
            prompt = f"""
            You are the Chief Quantitative Risk Auditor reviewing a LOSING closed trade on {trade.symbol}.
            Trader Profile: {agent_profile}
            Magic Number: {magic}

            === TRADE DETAILS ===
            - Ticket: {trade.ticket}
            - Side: {trade.side}
            - Volume / Lot Size: {trade.lot_size} lots
            - Entry Price: {trade.entry_price} | Exit Price: {trade.exit_price}
            - Stop Loss: {trade.stop_loss} | Take Profit: {trade.take_profit}
            - Realized PnL: ${trade.realized_pnl:.2f}
            - Technique Used: {trade.technique_used}
            - Original Entry Thesis: {trade.reason}
            - Invalidation Level: {trade.invalidation_condition}
            - Exit Trigger: {trade.exit_reason} (Danger Note: {trade.danger_trigger or 'N/A'})

            === LOSS ANALYSIS DIRECTIVE ===
            Analyze why this XAUUSD trade failed. Identify whether it was front-running a sweep, counter-trend friction, or news volatility. Formulate a 'Failure Anti-Pattern'.

            Output strictly a valid JSON object matching:
            {{
                "why_it_went_wrong": "Concise 2-3 sentence institutional explanation of why the setup failed",
                "failure_anti_pattern": "Concrete 1-2 sentence anti-pattern rule to prevent repeating this mistake"
            }}
            """

        candidate_models = [self.model_name, "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest"]
        for model in candidate_models:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2
                    )
                )
                data = json.loads(response.text)
                return data
            except Exception as e:
                logger.debug(f"Gemini PostMortem ({model}) call failed: {e}")
                continue

        return self._heuristic_fallback(trade, is_win=is_win)

    def _heuristic_fallback(self, trade: TradeJournal, is_win: bool = False) -> Dict[str, str]:
        """Resilient quantitative fallback segregated by trader magic number and win/loss outcome."""
        magic = trade.magic_number or 1001
        is_scalper = (magic == 2002)

        if is_win:
            if is_scalper:
                factor = f"[Scalp 2002] Accurate limit placement at session liquidity pool ({trade.entry_price}) during active volume."
                signature = "Execute limit orders at 50% FVG equilibrium during London/NY window with confirmed sweep rejection."
            else:
                factor = f"[Swing 1001] Macro trend alignment with D1/H4 flow from entry {trade.entry_price} with 1:3 R:R respected."
                signature = "Patience at H1/30m structural key zones with Alpha Vantage US 10Y Yield check cleared."
            return {"contributing_factor": factor, "winning_signature": signature}

        if is_scalper:
            if trade.exit_reason in ("PROACTIVE_EXHAUSTION_CLOSE", "EMERGENCY_DANGER_CLOSE"):
                why = (f"[Scalp 2002] Micro-momentum depleted at {trade.exit_price} ({trade.danger_trigger or 'Rejection wicks'}). "
                       f"Price entered choppy consolidation rather than immediate expansion from entry {trade.entry_price}.")
                lesson = "Avoid trading into compression or spread blowout; wait for clean liquidity sweep before executing scalps."
            elif trade.side == "BUY":
                why = (f"[Scalp 2002] 1m/5m liquidity sweep long at {trade.entry_price} was absorbed by persistent sell pressure. "
                       f"Price swept below micro-invalidation {trade.stop_loss}.")
                lesson = "Ensure London/NY session volume supports reversal and verify micro-CHoCH confirmation."
            else:
                why = (f"[Scalp 2002] 1m/5m liquidity sweep short at {trade.entry_price} failed to hold as buyers surged. "
                       f"Price broke above micro-invalidation {trade.stop_loss}.")
                lesson = "Do not short into strong session momentum without confirmed top rejection wick on 1m chart."
        else:
            if trade.exit_reason in ("PROACTIVE_EXHAUSTION_CLOSE", "EMERGENCY_DANGER_CLOSE"):
                why = (f"[Swing 1001] Market structure breached dangerous boundary ({trade.danger_trigger or 'Adverse displacement'}). "
                       f"Price moved against H1/H4 swing thesis from entry {trade.entry_price}.")
                lesson = "Cross-check US 10-Year Treasury Yield and DXY momentum before holding swing exposure through session opens."
            elif trade.side == "BUY":
                why = (f"[Swing 1001] Bullish structural swing from {trade.entry_price} failed due to higher-timeframe supply. "
                       f"Price broke structural swing low {trade.stop_loss}.")
                lesson = "Confirm H4 200 EMA alignment and verify macroeconomic yield tailwinds prior to swing long entries."
            else:
                why = (f"[Swing 1001] Bearish structural swing from {trade.entry_price} met strong institutional bid. "
                       f"Price breached swing high {trade.stop_loss}.")
                lesson = "Respect major daily demand zones and avoid shorting when US 10Y Yields are declining rapidly."

        return {"why_it_went_wrong": why, "failure_anti_pattern": lesson, "lessons_learned": lesson}

    @classmethod
    def check_scalper_circuit_breakers(cls, current_equity: Optional[float] = None) -> Tuple[bool, float, str]:
        """
        Quantitative Safeguards for Scalper (Magic 2002):
        1. 2 Consecutive Losses -> Automatic 30-minute execution freeze on scalper only.
        2. Cumulative Realized Daily Loss >= 2.0% -> Reduce scalper risk size to 0.15% (0.0015).
        Returns: (is_frozen: bool, effective_risk_pct: float, status_message: str)
        """
        db = SessionLocal()
        try:
            from datetime import timedelta
            now_utc = datetime.now(timezone.utc)
            start_of_day_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

            # --- Rule 1: Check 2 Consecutive Scalper Losses ---
            recent_scalp_trades = db.query(TradeJournal).filter(
                TradeJournal.magic_number == 2002,
                TradeJournal.closed_at.isnot(None)
            ).order_by(TradeJournal.closed_at.desc()).limit(2).all()

            if len(recent_scalp_trades) >= 2:
                t1, t2 = recent_scalp_trades[0], recent_scalp_trades[1]
                # Check if both were losses
                if (t1.realized_pnl < 0 or t1.status == "FAIL") and (t2.realized_pnl < 0 or t2.status == "FAIL"):
                    closed_at = t1.closed_at
                    if closed_at.tzinfo is None:
                        closed_at = closed_at.replace(tzinfo=timezone.utc)
                    elapsed_seconds = (now_utc - closed_at).total_seconds()
                    freeze_duration = 30 * 60  # 30 minutes in seconds

                    if elapsed_seconds < freeze_duration:
                        remaining_mins = (freeze_duration - elapsed_seconds) / 60.0
                        msg = f"SCALPER FREEZE ACTIVE: 2 consecutive losses. Execution frozen for another {remaining_mins:.1f} mins."
                        logger.warning(f"[Circuit Breaker] {msg}")
                        return True, 0.0, msg

            # --- Rule 2: Cumulative Realized Daily Loss Check (2.0% Threshold) ---
            today_trades = db.query(TradeJournal).filter(
                TradeJournal.closed_at >= start_of_day_utc
            ).all()

            total_daily_realized_pnl = sum(t.realized_pnl for t in today_trades if t.realized_pnl is not None)

            base_risk_pct = 0.005  # Default 0.50%
            if current_equity and current_equity > 0:
                daily_loss_pct = (abs(total_daily_realized_pnl) / current_equity) * 100.0 if total_daily_realized_pnl < 0 else 0.0
                if total_daily_realized_pnl < 0 and daily_loss_pct >= 2.0:
                    msg = (f"DAILY LOSS GUARD: Realized daily loss ${abs(total_daily_realized_pnl):.2f} "
                           f"({daily_loss_pct:.2f}% >= 2.0%). Scalper risk reduced from 0.50% to 0.15%.")
                    logger.warning(f"[Circuit Breaker] {msg}")
                    return False, 0.0015, msg

            return False, base_risk_pct, "Scalper operational (Normal risk 0.50%)."

        except Exception as e:
            logger.error(f"Error checking scalper circuit breakers: {e}")
            return False, 0.005, f"Circuit breaker check error: {e}"
        finally:
            db.close()

    @classmethod
    def get_recent_lessons(cls, limit: int = 5, magic_number: Optional[int] = None) -> List[str]:
        """Fetches the latest lessons learned from failed trades to provide context to strategy agents."""
        db = SessionLocal()
        try:
            query = db.query(TradeJournal).filter(
                TradeJournal.status == "FAIL",
                TradeJournal.lessons_learned.isnot(None)
            )
            if magic_number:
                query = query.filter(TradeJournal.magic_number == magic_number)

            failed_trades = query.order_by(TradeJournal.closed_at.desc()).limit(limit).all()
            lessons = [f"[{t.magic_number or 'ALL'}] {t.lessons_learned}" for t in failed_trades if t.lessons_learned]
            return lessons
        except Exception as e:
            logger.error(f"Error fetching recent lessons: {e}")
            return []
        finally:
            db.close()

