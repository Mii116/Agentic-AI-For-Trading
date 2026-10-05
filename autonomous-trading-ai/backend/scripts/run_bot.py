import os
import sys
import time
import logging
import schedule
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.data.mt5_data import MT5DataClient
from app.trading.mt5_engine import MT5ExecutionEngine
from app.trading.paper_engine import PaperTradingEngine
from app.agents.macro_director import MacroDirector
from app.agents.nfp_event import NFPEventHandler
from app.agents.swing_trader import SwingTrader
from app.agents.scalp_trader import ScalpTrader
from app.agents.arbiter import ChiefRiskArbiter
from app.trading.danger_sentry import DangerSentry
from app.trading.zone_monitor import ZoneRetestMonitor
from app.trading.macro_worker import AlphaVantageMacroWorker
from app.db.session import SessionLocal
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s'
)
logger = logging.getLogger("DualTraderOrchestrator")

TARGET_SYMBOL = "XAUUSD"

class InstitutionalBotOrchestrator:
    """
    Dual-Trader Orchestrator coordinating two independent strategy minds on MT5:
    1. Swing Trader (Magic: 1001) - Institutional swing setups (H4/H1/30m/15m) gated by US 10Y Yields
    2. Scalp Trader (Magic: 2002) - Micro-liquidity snipers (5m/1m) active during London/NY (07:00-17:00 UTC)
    3. Pyramiding Scale-In Engine - Scales into winning positions running >= 1:1 R:R with BE locked
    4. Danger Sentry - 3-second heartbeat proactive exhaustion exits & 50% partial TP / BE lock
    5. Alpha Vantage Macro Worker - Rate-limit safe 30-minute US 10Y Yield & DXY cache
    """

    def __init__(self):
        logger.info(f"Initializing Dual-Trader Institutional Hierarchy for {TARGET_SYMBOL}...")
        self.data_client = MT5DataClient()
        self.engine = MT5ExecutionEngine() if settings.MT5_ENABLED else PaperTradingEngine()
        
        self.macro_worker = AlphaVantageMacroWorker()
        self.macro_director = MacroDirector()
        self.nfp_handler = NFPEventHandler()
        self.swing_trader = SwingTrader()
        self.scalp_trader = ScalpTrader()
        self.zone_monitor = ZoneRetestMonitor(execution_engine=self.engine)
        self.arbiter = ChiefRiskArbiter(execution_engine=self.engine, zone_monitor=self.zone_monitor)
        self.danger_sentry = DangerSentry(execution_engine=self.engine)

        self.current_macro_regime = {
            "symbol": TARGET_SYMBOL,
            "regime": "NEUTRAL",
            "allow_long": True,
            "allow_short": True,
            "key_support": 0.0,
            "key_resistance": float("inf")
        }

    def update_macro_regime(self):
        """Higher Timeframe Cycle (D1 / H4 / H1) and Macro Caching run periodically."""
        logger.info(f"=== [MACRO CYCLE] Updating Macro Regime & Yield Caches for {TARGET_SYMBOL} ===")
        try:
            # 1. Update Alpha Vantage US 10Y Yield & DXY Cache
            try:
                self.macro_worker.update_macro_state()
            except Exception as e:
                logger.warning(f"Macro worker cache update warning: {e}")

            # 2. Fetch HTF bars
            bars_d1 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="1d", num_bars=30)
            bars_h4 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="4h", num_bars=60)
            bars_h1 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="1h", num_bars=60)
            
            if bars_d1:
                self.data_client.save_bars_to_db(bars_d1)
            if bars_h4:
                self.data_client.save_bars_to_db(bars_h4)
            if bars_h1:
                self.data_client.save_bars_to_db(bars_h1)

            if bars_h4 and bars_h1:
                regime = self.macro_director.analyze_regime(bars_h4, bars_h1, symbol=TARGET_SYMBOL)
                self.current_macro_regime = regime
                logger.info(
                    f"Macro Regime Updated: {regime['regime']} (Allow Long={regime['allow_long']}, "
                    f"Allow Short={regime['allow_short']}) | Support: {regime['key_support']} | Resistance: {regime['key_resistance']}"
                )
        except Exception as e:
            logger.error(f"Error in macro regime update: {e}")

    def run_execution_and_scouting_cycle(self):
        """Active Execution Cycle (M30, M15, M5, M1) run every 1 minute."""
        logger.info(f"--- [EXECUTION CYCLE] Dual-Trader Evaluation for {TARGET_SYMBOL} ---")
        try:
            # 1. Real-Time Danger Sentry Check (Emergency Auto-Close & Partial TP)
            self.danger_sentry.inspect_positions_for_danger(symbol=TARGET_SYMBOL)
            # Update NFP event state and incorporate its bias into macro regime
            self.nfp_handler.update_state()
            # Apply NFP bias to current macro regime if present
            if hasattr(self.nfp_handler, 'bias') and self.nfp_handler.bias != "NEUTRAL":
                if self.nfp_handler.bias == "BULLISH":
                    self.current_macro_regime["allow_long"] = True
                    self.current_macro_regime["allow_short"] = False
                elif self.nfp_handler.bias == "BEARISH":
                    self.current_macro_regime["allow_long"] = False
                    self.current_macro_regime["allow_short"] = True
                logger.info(f"[NFPHandler] Applied bias {self.nfp_handler.bias} to macro regime")

            # 2. Sync Balances, Positions, and Closed Deals (triggers dual-sided continuous post-mortem)
            if settings.MT5_ENABLED:
                self.engine.sync_account_balance()
                self.engine.sync_positions()
                self.engine.sync_closed_trades(symbol=TARGET_SYMBOL)

            # 3. Fetch Bars across timeframe stack
            bars_d1 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="1d", num_bars=30)
            bars_h4 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="4h", num_bars=40)
            bars_h1 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="1h", num_bars=40)
            bars_m30 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="30m", num_bars=40)
            bars_m15 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="15m", num_bars=40)
            bars_m5 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="5m", num_bars=40)
            bars_m1 = self.data_client.fetch_bars(symbol=TARGET_SYMBOL, timeframe="1m", num_bars=40)

            if bars_m30:
                self.data_client.save_bars_to_db(bars_m30)
            if bars_m15:
                self.data_client.save_bars_to_db(bars_m15)
            if bars_m5:
                self.data_client.save_bars_to_db(bars_m5)
            if bars_m1:
                self.data_client.save_bars_to_db(bars_m1)

            # 3B. Active Pending Limit Order Management (20m TTL & 5m CHoCH active invalidation)
            if settings.MT5_ENABLED and hasattr(self.engine, "manage_pending_limit_orders"):
                self.engine.manage_pending_limit_orders(symbol=TARGET_SYMBOL, m5_bars=bars_m5)

            # 3C. Feed fresh 5m bars to Danger Sentry
            if bars_m5:
                self.danger_sentry.inspect_positions_for_danger(symbol=TARGET_SYMBOL, m5_bars=bars_m5)

            candidate_proposals = []

            # 4. SWING TRADER EVALUATION (Magic: 1001)
            # Evaluates D1/H4/H1/30m/15m with Alpha Vantage US 10Y Yield Macro Gate
            if bars_h4 and bars_h1 and (bars_m30 or bars_m15):
                swing_prop = self.swing_trader.analyze_and_propose(
                    d1_bars=bars_d1 or [],
                    h4_bars=bars_h4,
                    h1_bars=bars_h1,
                    m30_bars=bars_m30 or [],
                    m15_bars=bars_m15 or [],
                    symbol=TARGET_SYMBOL
                )
                if swing_prop:
                    candidate_proposals.append(swing_prop)

            # 5. PULLBACK SCALE-IN (PYRAMIDING) EVALUATION (Magic: 1001)
            # Checks if an active Swing position is >= 1:1 R:R with BE locked and retesting FVG/equilibrium
            if bars_m15 and bars_m5:
                scale_in_prop = self.swing_trader.evaluate_scale_in(
                    m15_bars=bars_m15,
                    m5_bars=bars_m5,
                    h4_bars=bars_h4 or [],
                    h1_bars=bars_h1 or [],
                    symbol=TARGET_SYMBOL
                )
                if scale_in_prop:
                    candidate_proposals.append(scale_in_prop)

            # 6. SCALP TRADER EVALUATION (Magic: 2002)
            # Operates on M5/M1; restricted to London/NY window (07:00-17:00 UTC); macro exempt
            # Mandates strict 15-minute cooldown after any market exit
            from app.trading.cooldown_manager import MarketCooldownManager
            is_scalp_cooldown, rem_sec, cool_msg = MarketCooldownManager.check_scalper_cooldown()
            if is_scalp_cooldown:
                logger.info(f"[Scalp Trader (2002)] Scanning paused: {cool_msg}")
            elif bars_m5 and bars_m1:
                scalp_prop = self.scalp_trader.analyze_and_propose(
                    m5_bars=bars_m5,
                    m1_bars=bars_m1,
                    symbol=TARGET_SYMBOL
                )
                if scalp_prop:
                    candidate_proposals.append(scalp_prop)

            # 7. CHIEF RISK ARBITER GATEKEEPER & EXECUTION
            if candidate_proposals:
                logger.info(f"Chief Arbiter evaluating {len(candidate_proposals)} candidate proposal(s)...")
                approved = self.arbiter.arbitrate_proposals(
                    proposals=candidate_proposals,
                    macro_regime=self.current_macro_regime,
                    symbol=TARGET_SYMBOL
                )
                if approved:
                    logger.info(f"Arbiter APPROVED {len(approved)} trade(s)! Routing to MT5 with strict deviation=25...")
                    self.arbiter.execute_approved_trades(approved, symbol=TARGET_SYMBOL)
                else:
                    logger.info("Chief Arbiter rejected or withheld candidate proposals under risk rules.")

        except Exception as e:
            logger.error(f"Error in execution and scouting cycle: {e}")

def main():
    logger.info("=" * 70)
    logger.info("DUAL-TRADER INSTITUTIONAL HIERARCHY FOR XAUUSD")
    logger.info("Swing Trader (1001) | Scalp Trader (2002) | Shared Margin Guard")
    logger.info("=" * 70)

    orchestrator = InstitutionalBotOrchestrator()

    # Initial runs
    orchestrator.update_macro_regime()
    orchestrator.nfp_handler.update_state()
    orchestrator.run_execution_and_scouting_cycle()

    # Schedule Macro Director & Alpha Vantage Worker (every 15 minutes)
    schedule.every(15).minutes.do(orchestrator.update_macro_regime)

    # Schedule Execution & Scouting Cycle (every 1 minute)
    schedule.every(1).minutes.do(orchestrator.run_execution_and_scouting_cycle)

    logger.info("Institutional Dual-Trader running with 3s Proactive Danger Sentry heartbeat & 1s Zone Retest Monitor.")
    last_sentry_time = time.time()
    while True:
        schedule.run_pending()

        # Dynamic Zone-Retest Confirmation tick check (every 1s)
        try:
            orchestrator.zone_monitor.tick_check(symbol=TARGET_SYMBOL)
        except Exception as e:
            logger.error(f"Error in Zone Monitor tick: {e}")

        # Real-time Danger Sentry heartbeat (every 3 seconds)
        if time.time() - last_sentry_time >= 3.0:
            try:
                orchestrator.danger_sentry.inspect_positions_for_danger(symbol=TARGET_SYMBOL)
            except Exception as e:
                logger.error(f"Error in Danger Sentry heartbeat: {e}")
            last_sentry_time = time.time()

        time.sleep(1)

if __name__ == "__main__":
    main()

