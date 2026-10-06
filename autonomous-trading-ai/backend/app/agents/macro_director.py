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

        # Determine quantitative regimes for both timeframes
        h1_regime = h1_smc.get("regime", "NEUTRAL")
        h4_regime = h4_smc.get("regime", "NEUTRAL")

        # Baseline quantitative regime
        if h4_regime == "BULLISH" and h1_regime in ["BULLISH", "LEAN_BULLISH"]:
            quant_regime = "BULLISH"
        elif h4_regime == "BEARISH" and h1_regime in ["BEARISH", "LEAN_BEARISH"]:
            quant_regime = "BEARISH"
        elif "BULLISH" in h4_regime and "BULLISH" in h1_regime:
            quant_regime = "BULLISH"
        elif "BEARISH" in h4_regime and "BEARISH" in h1_regime:
            quant_regime = "BEARISH"
        else:
            # If H4 is bearish but H1 is breaking bullish (or vice-versa), classify as active relief/neutral
            quant_regime = "NEUTRAL"

        current_price = h1_smc["current_price"] or h4_smc["current_price"]

        # If H1 structure is bullish (trading above local EMAs), allow swing pullback longs
        # If H1 structure is bearish (trading below local EMAs), allow swing pullback shorts
        allow_long_quant = quant_regime in ["BULLISH", "NEUTRAL"] or "BULLISH" in h1_regime
        allow_short_quant = quant_regime in ["BEARISH", "NEUTRAL"] or "BEARISH" in h1_regime

        result = {
            "symbol": symbol,
            "regime": quant_regime,
            "h1_trend": h1_regime,
            "h4_trend": h4_regime,
            "current_price": current_price,
            "h4_ema_50": h4_smc["ema_50"],
            "h4_ema_200": h4_smc["ema_200"],
            "h1_ema_50": h1_smc["ema_50"],
            "h1_ema_200": h1_smc["ema_200"],
            "key_support": min(h4_smc["key_support"] or current_price, h1_smc["key_support"] or current_price),
            "key_resistance": max(h4_smc["key_resistance"] or current_price, h1_smc["key_resistance"] or current_price),
            "h4_order_blocks": h4_smc["order_blocks"],
            "h1_order_blocks": h1_smc["order_blocks"],
            "allow_long": allow_long_quant,
            "allow_short": allow_short_quant,
            "analysis_source": "100%_LOCAL_QUANT"
        }

        # Attempt to enrich with Gemini ONLY if cloud AI is explicitly enabled
        if settings.ENABLE_CLOUD_AI and self.client:
            ai_verdict = self._query_gemini_director(result, h4_smc, h1_smc)
            if ai_verdict:
                gemini_regime = ai_verdict.get("regime", quant_regime)
                result["regime"] = gemini_regime
                # Gemini regime respects H1 trend: do not veto H1 expansions purely on lagging H4 EMAs
                result["allow_long"] = (gemini_regime in ["BULLISH", "NEUTRAL"]) or ("BULLISH" in h1_regime)
                result["allow_short"] = (gemini_regime in ["BEARISH", "NEUTRAL"]) or ("BEARISH" in h1_regime)
                result["director_thesis"] = ai_verdict.get("thesis", "")
                result["analysis_source"] = "GEMINI_ENRICHED"

        logger.info(
            f"Macro Director Assessment for {symbol}: Regime={result['regime']} (H1={h1_regime}, H4={h4_regime}) | "
            f"Allow Long={result['allow_long']}, Allow Short={result['allow_short']} | "
            f"Key Range: [{result['key_support']} - {result['key_resistance']}]"
        )
        return result

    def _query_gemini_director(self, quant_data: Dict[str, Any], h4_smc: Dict[str, Any], h1_smc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prompt = f"""
        You are the Institutional Macro Director AI for Gold (XAUUSD).
        Analyze the Higher Timeframe (H4 / H1) market structure:
        - Current Price: {quant_data['current_price']}
        - H4 50 EMA: {h4_smc['ema_50']}, 200 EMA: {h4_smc['ema_200']} (Trend: {quant_data['h4_trend']})
        - H1 50 EMA: {h1_smc['ema_50']}, 200 EMA: {h1_smc['ema_200']} (Trend: {quant_data['h1_trend']})
        - H4 Key Range: Support {h4_smc['key_support']} | Resistance {h4_smc['key_resistance']}
        - H1 Key Range: Support {h1_smc['key_support']} | Resistance {h1_smc['key_resistance']}
        - Detected Order Blocks: H4={len(h4_smc['order_blocks'])}, H1={len(h1_smc['order_blocks'])}

        IMPORTANT INSTITUTIONAL RULE:
        If H1 structure is trading above local H1 EMAs with bullish momentum or expanding upward from support,
        classify the regime as 'BULLISH' or 'NEUTRAL' (relief expansion) to permit valid swing pullback entries.
        Do NOT rigidly output 'BEARISH' if active multi-session order flow is expanding upward.

        Output ONLY a valid JSON object with:
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
