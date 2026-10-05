import logging
import json
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types

from app.config.settings import settings
from app.indicators.smc import SMCAnalyzer

logger = logging.getLogger(__name__)

class MacroDirector:
    """
    Macro Director (HTF: H4 / H1):
    - Calculates 50 & 200 EMA trend and slope
    - Maps institutional order blocks and HTF liquidity pools
    - Outputs directional regime: 'BULLISH', 'BEARISH', or 'NEUTRAL'
    - Acts as high-level market filter for lower timeframe scouts
    """

    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def analyze_regime(self, h4_bars: List[Any], h1_bars: List[Any], symbol: str = "XAUUSD") -> Dict[str, Any]:
        """
        Analyzes H4 and H1 bars to determine overall institutional bias.
        """
        h4_smc = SMCAnalyzer.calculate_macro_regime(h4_bars)
        h1_smc = SMCAnalyzer.calculate_macro_regime(h1_bars)

        # Baseline quantitative regime
        if h4_smc["regime"] == "BULLISH" and h1_smc["regime"] in ["BULLISH", "LEAN_BULLISH"]:
            quant_regime = "BULLISH"
        elif h4_smc["regime"] == "BEARISH" and h1_smc["regime"] in ["BEARISH", "LEAN_BEARISH"]:
            quant_regime = "BEARISH"
        elif "BULLISH" in h4_smc["regime"] and "BULLISH" in h1_smc["regime"]:
            quant_regime = "BULLISH"
        elif "BEARISH" in h4_smc["regime"] and "BEARISH" in h1_smc["regime"]:
            quant_regime = "BEARISH"
        else:
            quant_regime = "NEUTRAL"

        current_price = h1_smc["current_price"] or h4_smc["current_price"]

        result = {
            "symbol": symbol,
            "regime": quant_regime,
            "current_price": current_price,
            "h4_ema_50": h4_smc["ema_50"],
            "h4_ema_200": h4_smc["ema_200"],
            "h1_ema_50": h1_smc["ema_50"],
            "h1_ema_200": h1_smc["ema_200"],
            "key_support": min(h4_smc["key_support"] or current_price, h1_smc["key_support"] or current_price),
            "key_resistance": max(h4_smc["key_resistance"] or current_price, h1_smc["key_resistance"] or current_price),
            "h4_order_blocks": h4_smc["order_blocks"],
            "h1_order_blocks": h1_smc["order_blocks"],
            "allow_long": quant_regime in ["BULLISH", "NEUTRAL"],
            "allow_short": quant_regime in ["BEARISH", "NEUTRAL"],
            "analysis_source": "QUANT_SMC"
        }

        # Attempt to enrich with Gemini if client is available
        if self.client:
            ai_verdict = self._query_gemini_director(result, h4_smc, h1_smc)
            if ai_verdict:
                result["regime"] = ai_verdict.get("regime", quant_regime)
                result["allow_long"] = result["regime"] in ["BULLISH", "NEUTRAL"]
                result["allow_short"] = result["regime"] in ["BEARISH", "NEUTRAL"]
                result["director_thesis"] = ai_verdict.get("thesis", "")
                result["analysis_source"] = "GEMINI_ENRICHED"

        logger.info(
            f"Macro Director Assessment for {symbol}: Regime={result['regime']} | "
            f"Allow Long={result['allow_long']}, Allow Short={result['allow_short']} | "
            f"Key Range: [{result['key_support']} - {result['key_resistance']}]"
        )
        return result

    def _query_gemini_director(self, quant_data: Dict[str, Any], h4_smc: Dict[str, Any], h1_smc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prompt = f"""
        You are the Institutional Macro Director AI for Gold (XAUUSD).
        Analyze the Higher Timeframe (H4 / H1) market structure:
        - Current Price: {quant_data['current_price']}
        - H4 50 EMA: {h4_smc['ema_50']}, 200 EMA: {h4_smc['ema_200']}
        - H1 50 EMA: {h1_smc['ema_50']}, 200 EMA: {h1_smc['ema_200']}
        - H4 Key Range: Support {h4_smc['key_support']} | Resistance {h4_smc['key_resistance']}
        - H1 Key Range: Support {h1_smc['key_support']} | Resistance {h1_smc['key_resistance']}
        - Detected Order Blocks: H4={len(h4_smc['order_blocks'])}, H1={len(h1_smc['order_blocks'])}

        Determine the macro directional regime. Output ONLY a valid JSON object with:
        {{
            "regime": "BULLISH" | "BEARISH" | "NEUTRAL",
            "thesis": "Concise 1-2 sentence institutional rationale",
            "confidence": float between 0.0 and 1.0
        }}
        """
        candidate_models = [self.model_name, "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest"]
        for model in candidate_models:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                data = json.loads(response.text)
                return data
            except Exception as e:
                logger.debug(f"Gemini MacroDirector ({model}) call failed: {e}")
                continue
        return None
