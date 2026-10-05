import os
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.trading.mt5_engine import MT5ExecutionEngine
import MetaTrader5 as mt5

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("Initializing MT5 Execution Engine...")
    engine = MT5ExecutionEngine()
    
    if not engine.enabled:
        logger.error("MT5 is disabled in settings. Check .env")
        return
        
    logger.info("Attempting to sync account balance...")
    engine.sync_account_balance()
    
    account_info = mt5.account_info()
    if account_info:
        logger.info(f"=== MT5 CONNECTION SUCCESS ===")
        logger.info(f"Account ID: {account_info.login}")
        logger.info(f"Server: {account_info.server}")
        logger.info(f"Balance: ${account_info.balance:.2f}")
        logger.info(f"Equity: ${account_info.equity:.2f}")
        logger.info(f"Margin Free: ${account_info.margin_free:.2f}")
        logger.info(f"Leverage: 1:{account_info.leverage}")
    else:
        logger.error(f"Failed to fetch account info from MT5. Error: {mt5.last_error()}")
        
    engine.shutdown()

if __name__ == "__main__":
    main()
