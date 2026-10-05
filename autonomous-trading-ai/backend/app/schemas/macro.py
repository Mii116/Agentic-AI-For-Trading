from typing import Optional
from datetime import datetime
from pydantic import BaseModel

class MacroStateResponse(BaseModel):
    timestamp: str
    us_10y_yield: float
    yield_delta: float
    yield_accelerating_up: bool
    dxy_trend: str
    allow_gold_swing_buy: bool
    macro_summary: str

class MacroRegimeResponse(BaseModel):
    symbol: str
    regime: str
    allow_long: bool
    allow_short: bool
    key_support: Optional[float] = None
    key_resistance: Optional[float] = None
    director_thesis: Optional[str] = None
