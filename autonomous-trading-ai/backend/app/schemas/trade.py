from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

class TradeCreate(BaseModel):
    symbol: str = Field(..., example="XAUUSD")
    side: str = Field(..., example="BUY")  # BUY or SELL
    lot_size: float = Field(..., gt=0, example=0.5)
    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    magic_number: int = Field(default=1001, example=1001)
    technique_used: Optional[str] = Field(default="SMC Order Block")
    reason: Optional[str] = Field(default="Algorithmic entry")

class TradeUpdate(BaseModel):
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    status: Optional[str] = None  # PASS, FAIL, BREAKEVEN
    exit_reason: Optional[str] = None
    why_it_went_wrong: Optional[str] = None
    lessons_learned: Optional[str] = None

class TradeJournalResponse(BaseModel):
    id: int
    ticket: Optional[int] = None
    symbol: str
    side: str
    lot_size: float
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    realized_pnl: Optional[float] = None
    status: str
    magic_number: Optional[int] = 1001
    partial_closed: Optional[bool] = False
    is_scale_in: Optional[bool] = False
    technique_used: Optional[str] = None
    reason: Optional[str] = None
    invalidation_condition: Optional[str] = None
    exit_reason: Optional[str] = None
    danger_trigger: Optional[str] = None
    why_it_went_wrong: Optional[str] = None
    lessons_learned: Optional[str] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class TradeJournalFilter(BaseModel):
    symbol: Optional[str] = None
    magic_number: Optional[int] = None
    status: Optional[str] = None  # PASS, FAIL, BREAKEVEN, OPEN
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
