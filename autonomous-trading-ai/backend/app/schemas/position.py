from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field

class PositionResponse(BaseModel):
    ticket: int
    symbol: str
    side: str
    volume: float
    price_open: float
    price_current: float
    stop_loss: float
    take_profit: float
    profit: float
    magic: int
    comment: Optional[str] = None
    opened_at: Optional[datetime] = None

class PositionCloseRequest(BaseModel):
    ticket: int = Field(..., description="MT5 Position Ticket")
    volume: Optional[float] = Field(default=None, description="Volume to close (default is full position)")

class PositionModifySLTP(BaseModel):
    ticket: int = Field(..., description="MT5 Position Ticket")
    stop_loss: Optional[float] = Field(default=None, description="New stop loss price")
    take_profit: Optional[float] = Field(default=None, description="New take profit price")
