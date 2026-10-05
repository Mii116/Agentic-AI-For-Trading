from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text
from datetime import datetime
from app.db.session import Base

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # BUY, SELL
    order_type = Column(String(20), default="MARKET")  # MARKET, LIMIT
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    status = Column(String(20), default="FILLED")  # PENDING, FILLED, CANCELLED, REJECTED
    mode = Column(String(20), default="PAPER")  # DEMO, BACKTEST, PAPER, MT5_DEMO, MT5_LIVE
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    filled_at = Column(DateTime, default=datetime.utcnow, nullable=True)

class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # LONG, SHORT
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    is_open = Column(Boolean, default=True, index=True)
    mode = Column(String(20), default="PAPER")
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)

class AccountBalance(Base):
    __tablename__ = "account_balances"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String(20), unique=True, default="PAPER", nullable=False)
    cash_balance = Column(Float, default=10000.0, nullable=False)
    equity = Column(Float, default=10000.0, nullable=False)
    used_margin = Column(Float, default=0.0, nullable=False)
    free_margin = Column(Float, default=10000.0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class TradeJournal(Base):
    __tablename__ = "trade_journals"

    id = Column(Integer, primary_key=True, index=True)
    ticket = Column(Integer, index=True, nullable=True)
    symbol = Column(String(20), nullable=False, default="XAUUSD", index=True)
    side = Column(String(10), nullable=False)  # BUY, SELL
    lot_size = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    realized_pnl = Column(Float, default=0.0)
    status = Column(String(20), default="OPEN", index=True)  # PASS, FAIL, BREAKEVEN, OPEN
    magic_number = Column(Integer, default=1001, index=True)  # 1001 for Swing, 2002 for Scalp
    partial_closed = Column(Boolean, default=False)
    is_scale_in = Column(Boolean, default=False)
    technique_used = Column(String(100), nullable=False)  # e.g. M5 Fair Value Gap, M15 BOS
    reason = Column(Text, nullable=False)  # Entry thesis and supporting confluences
    invalidation_condition = Column(Text, nullable=True)
    exit_reason = Column(String(50), nullable=True)  # TAKE_PROFIT, STOP_LOSS, EMERGENCY_DANGER_CLOSE, BREAKEVEN_SL, MANUAL
    danger_trigger = Column(String(200), nullable=True)
    why_it_went_wrong = Column(Text, nullable=True)  # AI Post-Mortem Diagnosis
    lessons_learned = Column(Text, nullable=True)
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

