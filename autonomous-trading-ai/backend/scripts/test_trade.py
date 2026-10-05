import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import SessionLocal
from app.models.strategy import TradingHypothesis
from app.trading.mt5_engine import MT5ExecutionEngine
from app.config.settings import settings
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_trade():
    db = SessionLocal()
    
    # 1. Create a fake "AI" Hypothesis that is highly confident to BUY Gold
    fake_hypothesis = TradingHypothesis(
        symbol="XAUUSD",
        strategy_type="TEST_MANUAL",
        direction="BUY",
        confidence=0.99,  # High confidence so it executes
        thesis="Manual test to prove execution engine works.",
        supporting_evidence="User requested live trade test.",
        counter_evidence="None",
        entry_price_estimate=0.0,
        suggested_stop_loss=0.0,
        suggested_take_profit=0.0,
        status="PROPOSED"
    )
    
    db.add(fake_hypothesis)
    db.commit()
    db.refresh(fake_hypothesis)
    logging.info(f"Created fake Hypothesis #{fake_hypothesis.id} for XAUUSD")
    
    # 2. Run the MT5 Execution Engine
    logging.info("Triggering MT5 Execution Engine...")
    
    # Force LIVE_TRADING_ENABLED just for this script
    settings.LIVE_TRADING_ENABLED = True
    
    engine = MT5ExecutionEngine()
    engine.process_new_hypotheses()
    
    logging.info("Test complete. Check your MT5 terminal!")

if __name__ == "__main__":
    test_trade()
