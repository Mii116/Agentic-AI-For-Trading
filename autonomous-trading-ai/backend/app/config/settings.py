from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os
from pathlib import Path

# Look for .env in current directory or parent directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = BASE_DIR.parent / ".env"

class Settings(BaseSettings):
    PROJECT_NAME: str = "Autonomous Trading AI Platform"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    
    # AI Engine
    GEMINI_API_KEY: Optional[str] = Field(default=None, env="GEMINI_API_KEY")
    GEMINI_MODEL: str = Field(default="gemini-3.5-flash", env="GEMINI_MODEL")
    
    # Market Data
    ALPHA_VANTAGE_API_KEY: str = Field(default="93ZPI584XNK8Z9ET", env="ALPHA_VANTAGE_API_KEY")
    
    # Database & Cache
    DATABASE_URL: str = Field(default="sqlite:///./trading.db", env="DATABASE_URL")
    REDIS_URL: Optional[str] = Field(default="redis://localhost:6379/0", env="REDIS_URL")
    
    # Trading Safety Model
    TRADING_MODE: str = Field(default="PAPER", env="TRADING_MODE")  # DEMO, BACKTEST, PAPER, MT5_DEMO, MT5_LIVE
    LIVE_TRADING_ENABLED: bool = Field(default=False, env="LIVE_TRADING_ENABLED")
    
    # Risk Limits & Leverage
    DEFAULT_CAPITAL: float = 10000.0
    LEVERAGE: float = Field(default=2000.0, env="LEVERAGE")
    MAX_POSITION_SIZE_PCT: float = 0.05  # Max 5% of portfolio per position
    MAX_PORTFOLIO_DRAWDOWN_PCT: float = 0.05  # 5% max drawdown circuit breaker
    DAILY_LOSS_LIMIT_PCT: float = 0.02  # 2% max daily loss circuit breaker
    DEFAULT_STOP_LOSS_PCT: float = 0.02  # 2% stop loss
    DEFAULT_TAKE_PROFIT_PCT: float = 0.04  # 4% take profit (1:2 risk/reward)
    
    # Target Assets
    DEFAULT_SYMBOLS: list[str] = ["XAUUSD", "BTCUSD"]
    
    # Cent Account & Multi-Entry Pyramiding Settings (200 - 1,000 USC)
    CENT_ACCOUNT_MODE: bool = Field(default=True, env="CENT_ACCOUNT_MODE")
    CENT_TRANCHE_MIN_LOT: float = Field(default=0.10, env="CENT_TRANCHE_MIN_LOT")
    CENT_TRANCHE_MAX_LOT: float = Field(default=0.20, env="CENT_TRANCHE_MAX_LOT")
    CENT_MAX_TRANCHES: int = Field(default=5, env="CENT_MAX_TRANCHES")
    CENT_FAST_BREAKEVEN_PIPS: float = Field(default=0.40, env="CENT_FAST_BREAKEVEN_PIPS")  # $0.40 move triggers BE
    CENT_TP1_PIPS: float = Field(default=1.20, env="CENT_TP1_PIPS")                       # $1.20 move triggers 50% partial TP

    # MT5 / Broker Configuration (Future HFM execution)
    MT5_ENABLED: bool = Field(default=False, env="MT5_ENABLED")
    MT5_MODE: str = Field(default="disabled", env="MT5_MODE")
    MT5_LOGIN: Optional[str] = Field(default=None, env="MT5_LOGIN")
    MT5_SERVER: Optional[str] = Field(default=None, env="MT5_SERVER")
    MT5_PASSWORD: Optional[str] = Field(default=None, env="MT5_PASSWORD")

    class Config:
        env_file = str(ENV_FILE)
        extra = "ignore"

settings = Settings()
