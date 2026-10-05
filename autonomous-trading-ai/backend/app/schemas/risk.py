from typing import Optional
from pydantic import BaseModel

class RiskMetricsResponse(BaseModel):
    balance: float
    equity: float
    margin_used: float
    free_margin: float
    margin_level_pct: float
    margin_utilization_pct: float
    shared_margin_safe: bool
    daily_drawdown_pct: float
    circuit_breaker_tripped: bool
    scalper_frozen: bool
    scalper_freeze_msg: Optional[str] = None
    scalper_effective_risk_pct: float

class AccountTierResponse(BaseModel):
    equity: float
    tier_name: str
    max_risk_pct: float
    max_concurrent_positions: int
    daily_drawdown_limit_pct: float
