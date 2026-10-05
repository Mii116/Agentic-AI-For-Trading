from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from datetime import datetime
from app.db.session import Base

class TradingHypothesis(Base):
    __tablename__ = "trading_hypotheses"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy_type = Column(String(50), nullable=False)  # MEAN_REVERSION, TREND_FOLLOWING, NEWS_CATALYST
    direction = Column(String(10), nullable=False)  # BUY, SELL, HOLD
    confidence = Column(Float, nullable=False)  # 0.0 to 1.0
    thesis = Column(Text, nullable=False)
    supporting_evidence = Column(Text, nullable=True)
    counter_evidence = Column(Text, nullable=True)
    entry_price_estimate = Column(Float, nullable=True)
    suggested_stop_loss = Column(Float, nullable=True)
    suggested_take_profit = Column(Float, nullable=True)
    status = Column(String(20), default="PROPOSED")  # PROPOSED, VALIDATED, REJECTED, EXECUTED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, index=True)
    strategy_name = Column(String(50), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    initial_capital = Column(Float, default=10000.0)
    final_equity = Column(Float, nullable=False)
    total_return_pct = Column(Float, nullable=False)
    sharpe_ratio = Column(Float, nullable=True)
    sortino_ratio = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=False)
    win_rate_pct = Column(Float, nullable=False)
    total_trades = Column(Integer, nullable=False)
    profit_factor = Column(Float, nullable=True)
    metrics_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
