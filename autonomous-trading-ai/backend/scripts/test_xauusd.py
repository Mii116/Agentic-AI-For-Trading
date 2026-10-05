import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import logging
from app.data.alpha_vantage import AlphaVantageClient
from app.agent.core import StrategyAgent
from app.trading.paper_engine import PaperTradingEngine
from app.db.session import SessionLocal
from app.models.market_data import MarketBar

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    client = AlphaVantageClient()
    symbol = "XAUUSD"
    
    logger.info("1. Fetching XAUUSD Forex bars...")
    # XAU is Gold, USD is US Dollar
    bars = client.fetch_forex_daily_bars(from_symbol="XAU", to_symbol="USD")
    
    if not bars:
        logger.error("Failed to fetch XAUUSD bars.")
        return
        
    logger.info(f"Fetched {len(bars)} daily bars. Saving to DB...")
    client.save_bars_to_db(bars)
    
    logger.info("2. Triggering AI Strategy Agent for XAUUSD...")
    db = SessionLocal()
    recent_bars = []
    try:
        recent_bars = db.query(MarketBar).filter_by(symbol=symbol).order_by(MarketBar.timestamp.desc()).limit(14).all()
        recent_bars.reverse()
    finally:
        db.close()
        
    if recent_bars:
        agent = StrategyAgent()
        hypothesis = agent.analyze_market_data(symbol=symbol, recent_bars=recent_bars)
        
        if hypothesis:
            logger.info("3. Triggering Paper Trading Engine...")
            engine = PaperTradingEngine()
            engine.process_new_hypotheses()
            
            # Print Account Status
            from scripts.test_paper_engine import print_account_status
            print_account_status()

if __name__ == "__main__":
    main()
