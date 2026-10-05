import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import engine, Base
# Import all models so they are registered with Base
from app.models.market_data import MarketBar
from app.models.strategy import TradingHypothesis
from app.models.trading import Order, Position, AccountBalance
from app.models.audit import AuditLog

def init_db():
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully.")

if __name__ == "__main__":
    init_db()
