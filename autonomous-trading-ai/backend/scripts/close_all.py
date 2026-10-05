import os
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.trading.mt5_engine import MT5ExecutionEngine
import MetaTrader5 as mt5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    engine = MT5ExecutionEngine()
    positions = mt5.positions_get()
    
    if not positions:
        print("No open positions to close.")
        return
        
    print(f"Found {len(positions)} open positions. Closing all...")
    for p in positions:
        side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
        print(f"Closing Ticket #{p.ticket}: {p.symbol} {side} {p.volume} lots...")
        success = engine.close_position(p.ticket)
        if success:
            print(f"Ticket #{p.ticket} closed successfully.")
        else:
            print(f"Failed to close Ticket #{p.ticket}.")
            
    engine.shutdown()

if __name__ == "__main__":
    main()
