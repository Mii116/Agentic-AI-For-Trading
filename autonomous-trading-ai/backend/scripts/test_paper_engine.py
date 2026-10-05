import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import logging
from app.trading.paper_engine import PaperTradingEngine
from app.db.session import SessionLocal
from app.models.trading import AccountBalance, Position

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def print_account_status():
    db = SessionLocal()
    try:
        account = db.query(AccountBalance).filter_by(mode="PAPER").first()
        if account:
            logger.info("=== PAPER ACCOUNT STATUS ===")
            logger.info(f"Equity: ${account.equity:.2f}")
            logger.info(f"Cash Balance: ${account.cash_balance:.2f}")
            logger.info(f"Used Margin: ${account.used_margin:.2f}")
            logger.info(f"Free Margin: ${account.free_margin:.2f}")
            
        positions = db.query(Position).filter_by(mode="PAPER", is_open=True).all()
        logger.info(f"=== OPEN POSITIONS ({len(positions)}) ===")
        for p in positions:
            logger.info(f"{p.side} {p.quantity} {p.symbol} @ ${p.entry_price:.2f}")
    finally:
        db.close()

def main():
    logger.info("Before execution:")
    print_account_status()
    
    logger.info("Running Paper Trading Engine...")
    engine = PaperTradingEngine()
    engine.process_new_hypotheses()
    
    logger.info("After execution:")
    print_account_status()

if __name__ == "__main__":
    main()
