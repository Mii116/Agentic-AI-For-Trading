from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

class StrategySignalResponse(BaseModel):
    proposal_id: str
    agent_role: str
    timeframe: str
    symbol: str
    direction: str
    entry_price: float
    structural_sl: float
    suggested_tp: float
    confluence_score: float
    risk_reward_ratio: float
    magic_number: int
    risk_pct: float
    is_scale_in: bool = False
    thesis: str
    supporting_confluences: List[str]
    timestamp: datetime

class HypothesisResponse(BaseModel):
    id: int
    symbol: str
    strategy_type: str
    direction: str
    confidence: float
    thesis: str
    supporting_evidence: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class StrategyPerformanceResponse(BaseModel):
    strategy_name: str
    magic_number: int
    total_trades: int
    win_rate: float
    profit_factor: float
    net_pnl: float
    avg_win: float
    avg_loss: float
    max_drawdown_dollars: float
