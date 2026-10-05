import os
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.data.mt5_data import MT5DataClient
from app.agent.core import StrategyAgent
from app.trading.mt5_engine import MT5ExecutionEngine
from app.db.session import SessionLocal
from app.models.market_data import MarketBar
from app.config.settings import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_single_cycle():
    logger.info("=== RUNNING VERIFICATION CYCLE ===")
    symbol = "XAUUSD"
    timeframe = "15m"
    
    client = MT5DataClient()
    agent = StrategyAgent()
    engine = MT5ExecutionEngine()
    db = SessionLocal()
    
    try:
        # 1. Fetch Market Data
        logger.info(f"1. Fetching {timeframe} data for {symbol}...")
        bars = client.fetch_bars(symbol=symbol, timeframe=timeframe, num_bars=30)
        if bars:
            saved = client.save_bars_to_db(bars)
            logger.info(f"Fetched {len(bars)} bars, saved {saved} new to DB.")
        else:
            logger.error("Failed to fetch bars from MT5.")
            return

        # 2. Get Recent Bars for AI
        recent_bars = db.query(MarketBar).filter_by(symbol=symbol, timeframe=timeframe).order_by(MarketBar.timestamp.desc()).limit(30).all()
        recent_bars.reverse()
        
        # 3. Call Gemini
        logger.info("2. Triggering Gemini 3.8 Flash AI Strategy Agent...")
        hyp = agent.analyze_market_data(symbol=symbol, timeframe=timeframe, recent_bars=recent_bars)
        
        if hyp:
            logger.info(f"Hypothesis generated successfully!")
            logger.info(f"  Direction: {hyp.direction}")
            logger.info(f"  Confidence: {hyp.confidence}")
            logger.info(f"  Thesis: {hyp.thesis}")
            logger.info(f"  SL: {hyp.suggested_stop_loss} | TP: {hyp.suggested_take_profit}")
            
            # 4. Run Execution Engine
            logger.info("3. Running Execution Engine...")
            engine.sync_account_balance()
            engine.sync_positions()
            engine.process_new_hypotheses()
        else:
            logger.warning("No hypothesis returned or an error occurred.")
            
    except Exception as e:
        logger.error(f"Error in test cycle: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    test_single_cycle()
