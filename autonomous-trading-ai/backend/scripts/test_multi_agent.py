import os
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.data.mt5_data import MT5DataClient
from app.trading.mt5_engine import MT5ExecutionEngine
from app.agents.macro_director import MacroDirector
from app.agents.structural_scout import StructuralScout
from app.agents.micro_sniper import MicroSniper
from app.agents.arbiter import ChiefRiskArbiter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TestMultiAgent")

def test_pipeline():
    logger.info("Initializing Multi-Timeframe Multi-Agent Pipeline Test...")
    symbol = "XAUUSD"
    data_client = MT5DataClient()
    engine = MT5ExecutionEngine()

    macro_director = MacroDirector()
    structural_scout = StructuralScout()
    micro_sniper = MicroSniper()
    arbiter = ChiefRiskArbiter(execution_engine=engine)

    # 1. Fetch Multi-Timeframe Bars
    logger.info("Fetching multi-timeframe bars (H4, H1, M15, M5, M1)...")
    bars_h4 = data_client.fetch_bars(symbol, "4h", 60)
    bars_h1 = data_client.fetch_bars(symbol, "1h", 60)
    bars_m15 = data_client.fetch_bars(symbol, "15m", 60)
    bars_m5 = data_client.fetch_bars(symbol, "5m", 60)
    bars_m1 = data_client.fetch_bars(symbol, "1m", 60)

    logger.info(f"Bars fetched: H4={len(bars_h4)}, H1={len(bars_h1)}, M15={len(bars_m15)}, M5={len(bars_m5)}, M1={len(bars_m1)}")

    # 2. Run Macro Director
    logger.info("Running Macro Director (H4 / H1)...")
    macro_regime = macro_director.analyze_regime(bars_h4, bars_h1, symbol=symbol)
    logger.info(f"Macro Director Result: Regime={macro_regime['regime']}, Range=[{macro_regime['key_support']} - {macro_regime['key_resistance']}]")

    # 3. Run Structural Scout
    logger.info("Running Structural Scout (M15)...")
    scout_proposal = structural_scout.scan_structure(bars_m15, macro_regime, symbol=symbol)
    if scout_proposal:
        logger.info(f"Scout Proposal: {scout_proposal.to_dict()}")
    else:
        logger.info("Scout: No M15 structural setup meeting threshold at this bar.")

    # 4. Run Micro Sniper
    logger.info("Running Micro Sniper (M5 / M1)...")
    sniper_proposal = micro_sniper.scan_micro_liquidity(bars_m5, bars_m1, macro_regime, symbol=symbol)
    if sniper_proposal:
        logger.info(f"Sniper Proposal: {sniper_proposal.to_dict()}")
    else:
        logger.info("Sniper: No M5/M1 session sweep/FVG setup meeting threshold at this bar.")

    # 5. Arbiter Gatekeeper & Lot Sizing
    proposals = [p for p in [scout_proposal, sniper_proposal] if p is not None]
    logger.info(f"Submitting {len(proposals)} proposal(s) to Chief Arbiter Gatekeeper...")

    approved_trades = arbiter.arbitrate_proposals(proposals, macro_regime, symbol=symbol)
    logger.info(f"Chief Arbiter Approved {len(approved_trades)} trade(s).")

    for p, lot, note in approved_trades:
        logger.info(f"Trade Approved: {p.direction} {lot} lots | R:R={p.risk_reward_ratio} | Confluence={p.confluence_score} | {note}")

    # 6. Test Breakeven Trailing Manager
    logger.info("Testing Breakeven Trailing Manager...")
    engine.sync_positions()
    engine.manage_breakeven_trailing(symbol=symbol)

    logger.info("Test Completed Successfully!")

if __name__ == "__main__":
    test_pipeline()
