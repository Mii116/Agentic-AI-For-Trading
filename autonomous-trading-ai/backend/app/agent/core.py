import logging
# Configurable multiplier for take‑profit (default 2.5× ATR)
TP_MULTIPLIER = 2.5
import time
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from app.config.settings import settings
from app.db.session import SessionLocal
from app.models.market_data import MarketBar
from app.models.strategy import TradingHypothesis

logger = logging.getLogger(__name__)

class TradingHypothesisSchema(BaseModel):
    strategy_type: str = Field(..., description="MEAN_REVERSION, TREND_FOLLOWING, or NEWS_CATALYST")
    direction: str = Field(..., description="BUY, SELL, HOLD, or CLOSE")
    confidence: float = Field(..., description="Confidence level between 0.0 and 1.0")
    thesis: str = Field(..., description="Main reasoning for the trade")
    supporting_evidence: str = Field(..., description="Evidence supporting the thesis")
    counter_evidence: str = Field(..., description="Risks or counter evidence")
    entry_price_estimate: float = Field(..., description="Estimated entry price")
    suggested_stop_loss: float = Field(..., description="Suggested stop loss price")
    suggested_take_profit: float = Field(..., description="Suggested take profit price")

class StrategyAgent:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("GEMINI_API_KEY not set. StrategyAgent will not work.")

    def analyze_market_data(self, symbol: str, timeframe: str, recent_bars: List[MarketBar]) -> Optional[TradingHypothesis]:
        if not self.client:
            logger.error("Cannot analyze, API key is missing.")
            return None

        # Format bars into a string
        bars_str = "\n".join([f"{b.timestamp}: O:{b.open} H:{b.high} L:{b.low} C:{b.close} V:{b.volume}" for b in recent_bars])
        
        mode = "SCALPING" if timeframe in ["1m", "5m", "15m"] else "SWING TRADING"

        from app.agent.indicators import calculate_technical_indicators
        indicators = calculate_technical_indicators(recent_bars)
        # Include retest validation flag
        retest_valid = indicators.get("retest_valid", True)
        if not retest_valid:
            logger.info("Retest validation failed; skipping hypothesis generation.")
            return None
        # Prepare indicator string after validation
        indicators_str = "\n".join([f"- {k.upper()}: {v}" for k, v in indicators.items()]) if indicators else "No indicators available (insufficient bars)."
        current_p = indicators.get("current_price", recent_bars[-1].close)
        atr_val = indicators.get("atr_14", 2.0) or 2.0
        
        prompt = f"""
        You are an elite quantitative AI trading algorithm. Your entire trading logic is strictly based on the following masterclass books:
        1. "Trading in the Zone" by Mark Douglas (Strict psychology and risk management).
        2. "Japanese Candlestick Charting Techniques" by Steve Nison (Candlestick pattern recognition).
        3. "Encyclopedia of Chart Patterns" by Thomas Bulkowski (Macro pattern breakouts and breakdowns).
        4. "Come Into My Trading Room" by Alexander Elder (The 3 M's: Mind, Method, Money).
        5. "Trade Your Way to Financial Freedom" by Van Tharp (Position sizing and expectancy).

        You are currently acting in a {mode} session analyzing the {timeframe} timeframe.

        === QUANTITATIVE TECHNICAL INDICATORS ===
        Current Price: {current_p}
        ATR (14 Volatility): {atr_val}
        {indicators_str}
        
        === RECENT PRICE ACTION ({symbol}) ===
        {bars_str}
        
        Using ONLY the strategies from these books and the technical indicators above:
        - Output DIRECTION as 'BUY', 'SELL', 'HOLD', or 'CLOSE' (if existing market structure has broken down).
        - Calculate confidence strictly on pattern alignment (e.g. Pinbars, Engulfing, EMA crossovers).
        - CRITICAL STOP LOSS & TAKE PROFIT RULES:
          * For {mode}: Suggested SL MUST be placed strictly 1.0x to 1.5x ATR from current price ({current_p}).
            Example for BUY: SL = {current_p} - (1.2 * {atr_val}) | TP = {current_p} + ({TP_MULTIPLIER} * {atr_val})
            Example for SELL: SL = {current_p} + (1.2 * {atr_val}) | TP = {current_p} - ({TP_MULTIPLIER} * {atr_val})
          * NEVER set SL or TP tens of dollars away for short timeframes. Keep them tight and executable!
        - If the market is choppy or there is no clear textbook setup, strictly output HOLD to preserve capital.
        """

        import time
        candidate_models = [self.model_name, "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest"]
        
        for model in candidate_models:
            max_retries = 2
            retry_delay = 3
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"Asking Gemini ({model}) for a hypothesis on {symbol}... (Attempt {attempt+1}/{max_retries})")
                    response = self.client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=TradingHypothesisSchema,
                            temperature=0.2,
                        ),
                    )
                    
                    # Response is JSON string matching TradingHypothesisSchema
                    import json
                    data = json.loads(response.text)
                    
                    # Save to DB
                    db = SessionLocal()
                    try:
                        hyp = TradingHypothesis(
                            symbol=symbol,
                            strategy_type=data["strategy_type"],
                            direction=data["direction"],
                            confidence=data["confidence"],
                            thesis=data["thesis"],
                            supporting_evidence=data["supporting_evidence"],
                            counter_evidence=data["counter_evidence"],
                            entry_price_estimate=data["entry_price_estimate"],
                            suggested_stop_loss=data["suggested_stop_loss"],
                            suggested_take_profit=data["suggested_take_profit"],
                            status="PROPOSED"
                        )
                        db.add(hyp)
                        db.commit()
                        db.refresh(hyp)
                        logger.info(f"Successfully generated hypothesis #{hyp.id} via {model}: {hyp.direction} {symbol}")
                        return hyp
                    except Exception as e:
                        db.rollback()
                        logger.error(f"Error saving hypothesis: {e}")
                        return None
                    finally:
                        db.close()
                        
                except Exception as e:
                    err_msg = str(e)
                    logger.error(f"Error calling {model} on attempt {attempt+1}: {err_msg}")
                    if "503" in err_msg or "UNAVAILABLE" in err_msg:
                        logger.warning(f"{model} is experiencing high demand.")
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
            
            if model != candidate_models[-1]:
                logger.info(f"Switching to fallback model ({candidate_models[-1]}) due to {model} unavailability...")

        logger.error(f"Failed to get hypothesis after trying candidate models.")
        return None
