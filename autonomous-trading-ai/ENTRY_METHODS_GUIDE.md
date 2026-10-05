# Comprehensive Institutional Entry & Execution Methodology
**Autonomous Trading AI Engine for XAUUSD (Gold) — Production Quant Specification**

This document details the multi-layered algorithmic and quantitative architecture used by the Autonomous Trading AI to scan, validate, gate, size, and execute trades on MT5.

---

## 🏗️ System Architecture Overview

The system operates as a **Dual-Trader Multi-Mind Architecture** coordinated by a strict **Chief Risk Arbiter**:

```mermaid
graph TD
    A[Market Price Feeds: MT5 Feeds] --> B[Multi-Timeframe Ingestion Stack: D1 / H4 / H1 / M30 / M15 / M5 / Tick]
    
    subgraph Strategy Minds
        B --> C[Swing Trader: Magic 1001]
        B --> D[Scalp Trader: Magic 2002 - 100% Deterministic Local Engine]
        B --> E[Pyramiding Scale-In Engine]
    end

    subgraph Intelligence & Gates
        C --> F1[Offline Macro / Post-Mortem Gate]
        D --> F2[Sub-Millisecond Deterministic Anti-Pattern Gate]
        F1 & F2 & E --> G[Chief Risk Arbiter Gatekeeper]
        G --> H[Dynamic Volatility Capital Manager: max(1.5xATR, $2.00)]
    end

    subgraph Execution & Monitoring
        H --> I1[MT5 Engine: Limit Orders]
        H --> I2[ZoneRetestMonitor: High-Frequency Tick Displacement Confirmation]
        I1 & I2 --> J[Danger Sentry: 3-Second Heartbeat & Confirmed M5 Structural Trailing]
    end
```

---

## 1. 🦅 Swing Trader Methodology (`Magic: 1001`)

The Swing Trader operates across **D1 / H4 / H1 / M30 / M15** to capture macro structural expansions.

### A. Macro & Yield Gate (Alpha Vantage Cached)
Before generating any swing hypothesis, the Macro Worker inspects the macro landscape:
1. **US 10-Year Treasury Yields (`TREASURY_YIELD`):**
   - Yields dropping / stable $\rightarrow$ **Bullish Gold Bias** (Approved).
   - Yields surging rapidly $\rightarrow$ **Bearish Gold Friction** (Longs Vetoed).
2. **US Dollar Index (`DXY` / `FX_DAILY`):**
   - Weak/Bearish USD $\rightarrow$ Enhances Gold Long setups.
   - Strong/Bullish USD $\rightarrow$ Enhances Gold Short setups.

### B. Structural Market Confirmation (Smart Money Concepts)
1. **Trend Identification (H4 / H1):**
   - Identifies higher highs / higher lows (**Bullish**) or lower highs / lower lows (**Bearish**).
   - Confirms Break of Structure (**BOS**) or Change of Character (**CHoCH**).
2. **Equilibrium Retracement:**
   - Uses the dealing range from the swing high to the swing low.
   - **Long Entry:** Must retrace into the **50% Discount Zone** or an unmitigated **Bullish Fair Value Gap (FVG) / Order Block**.
   - **Short Entry:** Must retrace into the **50% Premium Zone** or an unmitigated **Bearish FVG / Order Block**.
3. **Execution Mode:**
   - Employs **Targeted Limit Orders** (`BUY_LIMIT` / `SELL_LIMIT`) with a **20-minute Time-To-Live (TTL)**.
   - Automatically cancels if a confirmed 5m CHoCH invalidation occurs before fill.

---

## 2. ⚡ Scalp Trader Methodology (`Magic: 2002`)

The Scalp Trader ("The Liquid Sniper") is a **100% deterministic local Python engine** operating on closed M5 bars and live tick streams with **sub-15ms execution latency**. Zero LLM or network calls occur in the active execution path.

### A. Session Volatility Window Filter
- **Active Window:** **07:00 UTC to 17:00 UTC** (London & New York Session overlap).
- **Off-Hours Range:** All scalp entries are strictly blocked during low-liquidity Asian / late night hours to avoid spread widening and chop.

### B. Setup 1: Session Liquidity Sweep Retest (`M5_SWEEP_RETEST`)
1. **Asian / London High/Low Sweep:**
   - Evaluated exclusively on **closed M5 bars** with true-UTC session timestamps.
   - Detects when price wicks beyond PDH/PDL, Asian (00:00-07:00 UTC) or London (07:00-12:00 UTC) extremes by $\ge \max(\$0.10, 0.05 \times \text{ATR})$ and closes back inside with a pronounced rejection wick ($\ge 40\%$).
2. **Smart Zone Retest (No Passive 50% Limits):**
   - Arms a mitigation zone between the sweep extreme and the swept level.
   - **Invalidation Knife Filter:** If price trades through the sweep extreme, the zone is disarmed instantly with **zero capital lost**.
   - **Displacement Confirmation:** Waits for price to enter the zone and fire a market order ONLY when a displacement confirmation tick appears in the intended direction (higher than prior 3 ticks for BUY, lower for SELL, plus $\ge \$0.15$ bounce from the touch extreme).

### C. Setup 2: Fresh Institutional M5 FVG Mitigation (`M5_FVG_RETEST`)
1. **Displacement:** Identifies 3-candle imbalance gaps formed with displacement body $\ge 0.80 \times \text{ATR}$ and gap size $\ge 0.25 \times \text{ATR}$.
2. **Freshness Filter:** Only untested, first-touch gaps are eligible.
3. **Execution:** Armed in the `ZoneRetestMonitor` and dispatched as a confirmed market entry upon tick momentum.

### D. Volatility-Adjusted Stop Loss Sizing
Fixed dollar stops ($0.50) are eliminated. All Gold scalps dynamically size stops:
$$\text{SL Distance} = \max\left(\text{Structural Distance},\ 1.5 \times \text{ATR}(14, \text{M5}),\ \$2.00\right)$$
- **Hard Floor:** No stop loss on Gold may ever be tighter than **$1.50** under any circumstances, guaranteeing broker spreads (25–45 points) do not consume the stop distance.
- **Take Profit:** Dynamic **1:2.0 R:R** computed from the actual confirmation fill price.

### E. Mandatory Post-Exit Cooldown
- Whenever a scalper trade exits (Take-Profit, Stop-Loss, or Sentry exit), a **15-minute mandatory cooldown** is enforced to eliminate revenge trading.

---

## 3. 🛡️ Chief Risk Arbiter Gatekeeper

The Arbiter serves as the final mathematical barrier before routing orders to MT5 or the Zone Retest Monitor:

| Gate | Check | Threshold / Rule |
| :--- | :--- | :--- |
| **Circuit Breaker** | Portfolio Drawdown | Max **5% overall** or **2% daily** drawdown stops all trading. |
| **Shared Margin Guard** | Account Margin Utilization | Total margin across all positions cannot exceed **20%**. |
| **Spread Guard** | Real-Time Broker Spread | Blocks orders if Gold spread exceeds **50 points ($0.50)**. |
| **News Blackout** | USD High-Impact Calendar | Freezes new entries **30m before** and **15m after** CPI, NFP, FOMC. |
| **Zone Debounce** | Repeated Stopout Protection | Blacklists entries within **$3.00** of recent loss for 20 minutes. |
| **Experience Matrix** | Pattern Historical Confidence | Requires $\ge 60\%$ historical win-rate or demands H1 trend confirmation. |
| **Min Stop Distance** | Gold Spread Protection | Automatically widens any stop tighter than **$1.50** before sizing. |

---

## 4. 🎯 Smart Pending & Zone Retest Confirmation (`ZoneRetestMonitor`)

To eliminate the quant trap of **adverse selection** (where passive limit orders miss runaway winners and catch falling knives), scalp setups use dynamic zone monitoring:

```mermaid
graph TD
    Z1[Scalp Trader arms Zone [zone_low, zone_high]] --> Z2{Did price trade through invalidation?}
    Z2 -- YES --> Z3[Disarm Zone Immediately: ZERO Capital Lost / Knife Filtered]
    Z2 -- NO --> Z4{Did price enter mitigation zone?}
    Z4 -- YES --> Z5{Displacement Confirmation Tick in trade direction?}
    Z5 -- YES --> Z6[Fire Fast Market Order with Volatility-Adjusted SL & TP]
    Z5 -- NO --> Z7[Wait for Confirmation or TTL Expiry]
```

1. **Knife Invalidation:** If price blows through the invalidation boundary, the zone is disarmed before an order is placed.
2. **Displacement Confirmation:** Price must touch into the zone and print a tick confirming rejection momentum ($\ge \$0.15$ rebound and beating prior 3 ticks) before executing.

---

## 5. 🚨 Danger Sentry (Reformed Exit & Structural Trailing)

Positions are monitored on a **3-second heartbeat** by the **Danger Sentry**:

### A. Muted Early Exhaustion Exits (Confirmed M5 Closes Only)
- 1-minute counter-wicks and mid-candle noise are **completely eliminated**.
- Exhaustion exits evaluate **ONLY on confirmed 5-minute (M5) candle closes** and require at least **2 consecutive rejection candles with declining volume**.

### B. Structural Trailing Instead of Hard Breakeven
- **50% Partial Take-Profit at 1:1 R:R:** Banks 1R profit on half the position.
- **Structural Trailing Stop:** Rather than clamping the stop to flat entry $+ \$0.20$ (which chokes runners during normal retests), the stop is placed behind the most recent confirmed **M5 swing low (for Longs)** or **M5 swing high (for Shorts)** buffered by ATR.
- **Spread Cost Locked:** Guaranteed not to exceed spread costs if the swing low is above entry, while maintaining $\ge \$1.50$ buffer from market price.
- **Continuous Trailing:** As price expands, the stop ratchets forward behind new M5 swing pivots, letting runners achieve multi-R statistical expectancy.

### C. Removal of Premature Cuts
- The arbitrary 85% stop boundary emergency cut has been removed. Stop loss execution is left to the MT5 engine and confirmed M5 structural closes.

---

## 6. 📊 Production-Grade Quant Setup Comparison

| Component | Legacy Flawed Setup | Production-Grade Quant Standard (Active) |
| :--- | :--- | :--- |
| **Scalper Execution** | Latency-bound (Calls Gemini Flash per setup) | **Zero-latency local Python** ($< 15\text{ms}$ closed-bar scan) |
| **Stop Loss Sizing** | Fixed $0.50 (eaten by broker spread) | **Dynamic Volatility:** $\ge 1.5 \times \text{ATR(M5)}$, Floor $\$1.50 - \$2.00$ |
| **Limit Order Placement**| Static 50% midpoint (adverse selection) | **Dynamic Zone-Retest Confirmation** with Knife Filter |
| **Exit Management** | 1m/5m wicks & premature breakeven choke | **Confirmed M5 candle close only**; ATR-buffered structural trailing |
| **Cooldown Rules** | 15m post-exit cooldown | **Dynamic Volatility Cooldown:** 15m exit cooldown + $3.00 price zone debounce |
