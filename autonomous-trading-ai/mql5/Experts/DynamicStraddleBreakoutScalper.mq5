//+------------------------------------------------------------------+
//|                                DynamicStraddleBreakoutScalper.mq5|
//|               Institutional Straddle / Bracket Breakout Scalper |
//|                             Low-Latency OCO & Micro-Ratchet Trail|
//+------------------------------------------------------------------+
#property copyright   "Autonomous Trading AI Engine"
#property link        "https://github.com/Mii116/Agentic-AI-For-Trading"
#property version     "2.00"
#property description "Low-latency institutional straddle breakout scalper for XAUUSD (Gold)."
#property description "Features dynamic ATR brackets, sub-tick rate limiting, sub-50ms OCO cancellation,"
#property description "and a two-tier micro-ratchet trailing stop engine."

//--- Standard Library Includes
#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//+------------------------------------------------------------------+
//| Enums & Types                                                    |
//+------------------------------------------------------------------+
enum ENUM_EA_STATE
{
   STATE_HUNTING,    // Dynamically placing & maintaining Buy Stop / Sell Stop brackets
   STATE_IN_TRADE,   // Active filled position; OCO triggered; managing ratchet trailing
   STATE_COOLDOWN    // Minimum delay post-exit to prevent churn & whipsaw
};

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
input group "=== Institutional Scalper Core ==="
input ulong             InpMagic                = 2002;             // Magic Number (Scalp Sniper Isolation)
input string            InpOrderComment         = "StraddleScalp";  // Execution Order Comment
input double            InpFixedLots            = 0.10;             // Sizing (Lots) (Set 0.0 for Risk %)
input double            InpRiskPercent          = 0.50;             // Dynamic Risk % per Trade (if LotSize=0)

input group "=== Dynamic Bracket & Rate Limiting ==="
input int               InpATRPeriod            = 14;               // ATR Period (M1 Timeframe)
input double            InpATRFactor            = 1.2;              // ATR Distance Multiplier
input int               InpMinDistancePoints    = 40;               // Minimum Bracket Offset (Points / $0.40)
input int               InpRepositionStepPoints = 25;               // Rate-Limit Reposition Threshold (Points)
input int               InpMaxSpreadPoints      = 35;               // Max Spread Filter (Points / $0.35)

input group "=== Order Protection & Initial Targets ==="
input int               InpInitialSLPoints      = 80;               // Initial Stop Loss Distance (Points / $0.80)
input int               InpInitialTPPoints      = 160;              // Initial Take Profit Distance (Points / $1.60) (0 = Off)
input int               InpMaxSlippagePoints    = 15;               // Max Allowed Slippage Deviation (Points)
input int               InpOrderTTLMinutes      = 10;               // Pending Order Expiration TTL (Minutes)

input group "=== Micro-Ratchet Trailing Engine ==="
input bool              InpEnableRatchetTrail   = true;             // Enable Micro-Ratchet Engine
input int               InpBETriggerPoints      = 40;               // Breakeven Activation Threshold (Points / +$0.40)
input int               InpBEBufferPoints       = 10;               // Breakeven Profit Lock-in Buffer (Points / +$0.10)
input int               InpTrailDistancePoints  = 30;               // Trailing Stop Distance from Peak (Points)
input int               InpTrailStepPoints      = 15;               // Ratchet Step Advancement (Points)

input group "=== State Machine & Execution Limits ==="
input int               InpCooldownSeconds      = 15;               // Post-Exit Cooldown Time (Seconds)
input bool              InpShowDashboard        = true;             // Render On-Chart Telemetry Dashboard

//+------------------------------------------------------------------+
//| Global Internal Variables & Objects                             |
//+------------------------------------------------------------------+
CTrade                  m_trade;
CPositionInfo           m_position;
COrderInfo              m_order;
CAccountInfo            m_account;
CSymbolInfo             m_symbol;

ENUM_EA_STATE           m_state                 = STATE_HUNTING;
datetime                m_cooldown_expiry       = 0;
int                     m_atr_handle            = INVALID_HANDLE;

// Active Pending Bracket Tracking
ulong                   m_buy_stop_ticket       = 0;
ulong                   m_sell_stop_ticket      = 0;
double                  m_last_buy_stop_price   = 0.0;
double                  m_last_sell_stop_price  = 0.0;

// Trailing Ratchet Tracking
ulong                   m_active_pos_ticket     = 0;
bool                    m_be_locked             = false;
double                  m_highest_peak_price    = 0.0;
double                  m_lowest_peak_price     = 0.0;

//+------------------------------------------------------------------+
//| Expert Initialization Function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // 1. Initialize Symbol & Account wrappers
   if(!m_symbol.Name(_Symbol))
   {
      PrintFormat("[INIT ERROR] Failed to initialize symbol %s", _Symbol);
      return INIT_FAILED;
   }
   m_symbol.Refresh();

   // 2. Configure CTrade execution properties
   m_trade.SetExpertMagicNumber(InpMagic);
   m_trade.SetDeviationInPoints(InpMaxSlippagePoints);

   // Auto-detect fastest supported execution filling mode (IOC > FOK > RETURN)
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else if((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);

   // 3. Initialize M1 ATR Indicator
   m_atr_handle = iATR(_Symbol, PERIOD_M1, InpATRPeriod);
   if(m_atr_handle == INVALID_HANDLE)
   {
      PrintFormat("[INIT ERROR] Failed to create iATR handle for %s on M1", _Symbol);
      return INIT_FAILED;
   }

   // 4. Initial state synchronization
   SyncCurrentPositionsAndOrders();

   PrintFormat("==========================================================");
   PrintFormat("DYNAMIC STRADDLE BREAKOUT SCALPER INITIALIZED SUCCESSFULLY");
   PrintFormat("Symbol: %s | Magic: %I64u | Filling: %s", 
               _Symbol, InpMagic, EnumToString(m_trade.RequestTypeFilling()));
   PrintFormat("Min Distance: %d pts | Reposition Step: %d pts | Max Spread: %d pts", 
               InpMinDistancePoints, InpRepositionStepPoints, InpMaxSpreadPoints);
   PrintFormat("==========================================================");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert Deinitialization Function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Release ATR indicator handle
   if(m_atr_handle != INVALID_HANDLE)
      IndicatorRelease(m_atr_handle);

   // Clean up on-chart comments
   Comment("");

   PrintFormat("[DEINIT] Dynamic Straddle Scalper deinitialized. Reason code: %d", reason);
}

//+------------------------------------------------------------------+
//| Expert Tick Function (Core Latency-Critical Loop)                |
//+------------------------------------------------------------------+
void OnTick()
{
   // Refresh live market book quotes
   if(!m_symbol.RefreshRates())
      return;

   // 1. Evaluate Spread Guard Filter
   long current_spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(current_spread > InpMaxSpreadPoints)
   {
      // Excessive spread detected: protect against blowouts by disarming resting brackets
      if(m_state == STATE_HUNTING)
      {
         PurgePendingBrackets("Spread Exceeded Max Filter Limit");
      }
      UpdateOnChartHUD(current_spread, "WIDE SPREAD FILTER ACTIVE");
      return;
   }

   // 2. State Machine Dispatcher
   switch(m_state)
   {
      case STATE_COOLDOWN:
         ProcessCooldownState(current_spread);
         break;

      case STATE_IN_TRADE:
         ProcessInTradeState(current_spread);
         break;

      case STATE_HUNTING:
      default:
         ProcessHuntingState(current_spread);
         break;
   }

   // 3. Render On-Chart HUD
   if(InpShowDashboard)
      UpdateOnChartHUD(current_spread, EnumToString(m_state));
}

//+------------------------------------------------------------------+
//| Immediate OCO Engine (Event-Driven Transaction Handler)          |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   // Listen strictly for deal additions to execute instantaneous sub-50ms OCO
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      ulong deal_ticket = trans.deal;
      if(HistoryDealSelect(deal_ticket))
      {
         ulong deal_magic   = HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
         string deal_symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
         long deal_entry    = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);

         if(deal_magic == InpMagic && deal_symbol == _Symbol)
         {
            // === SCENARIO A: A PENDING BRACKET HAS FILLED (DEAL_ENTRY_IN) ===
            if(deal_entry == DEAL_ENTRY_IN)
            {
               double fill_price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
               ulong order_id    = HistoryDealGetInteger(deal_ticket, DEAL_ORDER);
               long deal_type    = HistoryDealGetInteger(deal_ticket, DEAL_TYPE);

               // Slippage audit calculation
               double target_order_price = (deal_type == DEAL_TYPE_BUY) ? m_last_buy_stop_price : m_last_sell_stop_price;
               double slippage_points = 0.0;
               if(target_order_price > 0.0)
                  slippage_points = MathAbs(fill_price - target_order_price) / _Point;

               PrintFormat(">>> [INSTANT OCO FILL] Deal #%I64u filled (Order #%I64u) at %.2f | Target: %.2f | Slippage: %.1f pts",
                           deal_ticket, order_id, fill_price, target_order_price, slippage_points);

               // 1. INSTANT OCO: Execute immediate deletion of the counterpart order (< 50ms)
               PurgePendingBrackets("OCO Counterpart Cancellation");

               // 2. Initialize In-Trade State tracking
               m_state = STATE_IN_TRADE;
               m_active_pos_ticket = HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
               m_be_locked = false;
               m_highest_peak_price = fill_price;
               m_lowest_peak_price  = fill_price;
            }
            // === SCENARIO B: ACTIVE POSITION HAS CLOSED (DEAL_ENTRY_OUT) ===
            else if(deal_entry == DEAL_ENTRY_OUT)
            {
               double exit_profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
               PrintFormat(">>> [POSITION CLOSED] Deal #%I64u Exit complete. Realized PnL: $%.2f",
                           deal_ticket, exit_profit);

               // Clear tracking variables
               m_active_pos_ticket = 0;
               m_be_locked = false;

               // Enter post-exit cooldown state
               m_state = STATE_COOLDOWN;
               m_cooldown_expiry = TimeCurrent() + InpCooldownSeconds;
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| State 1: STATE_COOLDOWN Handler                                  |
//+------------------------------------------------------------------+
void ProcessCooldownState(long current_spread)
{
   // Check if cooldown timer has elapsed
   if(TimeCurrent() >= m_cooldown_expiry)
   {
      PrintFormat("[STATE] Cooldown of %d seconds elapsed. Transitioning to STATE_HUNTING.", InpCooldownSeconds);
      m_state = STATE_HUNTING;
      m_cooldown_expiry = 0;
   }
}

//+------------------------------------------------------------------+
//| State 2: STATE_HUNTING Handler (Bracket Placement & Repositioning|
//+------------------------------------------------------------------+
void ProcessHuntingState(long current_spread)
{
   // 1. Safety verification: If a position is already open, force transition to STATE_IN_TRADE
   if(HasOpenPosition())
   {
      m_state = STATE_IN_TRADE;
      return;
   }

   // 2. Calculate Dynamic Offset based on M1 ATR and Minimum Point bounds
   double atr_val = GetCurrentM1ATR();
   double atr_points = (atr_val > 0.0) ? (atr_val / _Point) * InpATRFactor : (double)InpMinDistancePoints;
   double offset_points = MathMax(atr_points, (double)InpMinDistancePoints);

   // Respect broker minimal stop level
   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stops_level > 0 && offset_points < (double)(stops_level + 5))
      offset_points = (double)(stops_level + 5);

   double ask = m_symbol.Ask();
   double bid = m_symbol.Bid();

   double target_buy_stop  = NormalizeDouble(ask + (offset_points * _Point), _Digits);
   double target_sell_stop = NormalizeDouble(bid - (offset_points * _Point), _Digits);

   double lot_size = CalculateTradeLotSize();

   // Synchronize existing pending tickets
   LocateActivePendingOrders();

   datetime expiration_time = (InpOrderTTLMinutes > 0) ? (TimeCurrent() + (InpOrderTTLMinutes * 60)) : 0;

   // 3. Maintain / Place BUY STOP Bracket
   if(m_buy_stop_ticket == 0)
   {
      double sl = (InpInitialSLPoints > 0) ? NormalizeDouble(target_buy_stop - (InpInitialSLPoints * _Point), _Digits) : 0.0;
      double tp = (InpInitialTPPoints > 0) ? NormalizeDouble(target_buy_stop + (InpInitialTPPoints * _Point), _Digits) : 0.0;

      if(m_trade.BuyStop(lot_size, target_buy_stop, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, expiration_time, InpOrderComment))
      {
         m_buy_stop_ticket = m_trade.ResultOrder();
         m_last_buy_stop_price = target_buy_stop;
         PrintFormat("[BRACKET] Placed BUY STOP #%I64u at %.2f (SL: %.2f, TP: %.2f)", 
                     m_buy_stop_ticket, target_buy_stop, sl, tp);
      }
   }
   else
   {
      // Rate-limited repositioning check
      double drift_points = MathAbs(target_buy_stop - m_last_buy_stop_price) / _Point;
      if(drift_points >= InpRepositionStepPoints)
      {
         double sl = (InpInitialSLPoints > 0) ? NormalizeDouble(target_buy_stop - (InpInitialSLPoints * _Point), _Digits) : 0.0;
         double tp = (InpInitialTPPoints > 0) ? NormalizeDouble(target_buy_stop + (InpInitialTPPoints * _Point), _Digits) : 0.0;

         if(m_trade.OrderModify(m_buy_stop_ticket, target_buy_stop, sl, tp, ORDER_TIME_SPECIFIED, expiration_time))
         {
            m_last_buy_stop_price = target_buy_stop;
            PrintFormat("[BRACKET UPDATE] Repositioned BUY STOP #%I64u to %.2f (Drift: %.1f pts)", 
                        m_buy_stop_ticket, target_buy_stop, drift_points);
         }
      }
   }

   // 4. Maintain / Place SELL STOP Bracket
   if(m_sell_stop_ticket == 0)
   {
      double sl = (InpInitialSLPoints > 0) ? NormalizeDouble(target_sell_stop + (InpInitialSLPoints * _Point), _Digits) : 0.0;
      double tp = (InpInitialTPPoints > 0) ? NormalizeDouble(target_sell_stop - (InpInitialTPPoints * _Point), _Digits) : 0.0;

      if(m_trade.SellStop(lot_size, target_sell_stop, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, expiration_time, InpOrderComment))
      {
         m_sell_stop_ticket = m_trade.ResultOrder();
         m_last_sell_stop_price = target_sell_stop;
         PrintFormat("[BRACKET] Placed SELL STOP #%I64u at %.2f (SL: %.2f, TP: %.2f)", 
                     m_sell_stop_ticket, target_sell_stop, sl, tp);
      }
   }
   else
   {
      // Rate-limited repositioning check
      double drift_points = MathAbs(target_sell_stop - m_last_sell_stop_price) / _Point;
      if(drift_points >= InpRepositionStepPoints)
      {
         double sl = (InpInitialSLPoints > 0) ? NormalizeDouble(target_sell_stop + (InpInitialSLPoints * _Point), _Digits) : 0.0;
         double tp = (InpInitialTPPoints > 0) ? NormalizeDouble(target_sell_stop - (InpInitialTPPoints * _Point), _Digits) : 0.0;

         if(m_trade.OrderModify(m_sell_stop_ticket, target_sell_stop, sl, tp, ORDER_TIME_SPECIFIED, expiration_time))
         {
            m_last_sell_stop_price = target_sell_stop;
            PrintFormat("[BRACKET UPDATE] Repositioned SELL STOP #%I64u to %.2f (Drift: %.1f pts)", 
                        m_sell_stop_ticket, target_sell_stop, drift_points);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| State 3: STATE_IN_TRADE Handler (Micro-Ratchet Trailing Engine)  |
//+------------------------------------------------------------------+
void ProcessInTradeState(long current_spread)
{
   // Find active position for this EA
   if(!SelectActivePosition())
   {
      // No active position found; transition back to hunting
      m_state = STATE_HUNTING;
      return;
   }

   // Safety Check: Ensure no orphan pending brackets remain active
   PurgePendingBrackets("In-Trade Orphan Order Cleanup");

   if(!InpEnableRatchetTrail)
      return;

   ENUM_POSITION_TYPE pos_type = m_position.PositionType();
   double open_price           = m_position.PriceOpen();
   double current_sl           = m_position.StopLoss();
   double current_tp           = m_position.TakeProfit();
   ulong  pos_ticket           = m_position.Ticket();

   double bid = m_symbol.Bid();
   double ask = m_symbol.Ask();

   // ===================================================================
   // BUY POSITION MICRO-RATCHET ENGINE
   // ===================================================================
   if(pos_type == POSITION_TYPE_BUY)
   {
      double gain_points = (bid - open_price) / _Point;

      // Track peak expansion price
      if(bid > m_highest_peak_price)
         m_highest_peak_price = bid;

      // --- TIER 1: FAST BREAKEVEN FLOOR (+InpBETriggerPoints) ---
      if(gain_points >= InpBETriggerPoints && !m_be_locked)
      {
         double target_be_sl = NormalizeDouble(open_price + (InpBEBufferPoints * _Point), _Digits);
         if(current_sl < target_be_sl)
         {
            if(m_trade.PositionModify(pos_ticket, target_be_sl, current_tp))
            {
               m_be_locked = true;
               PrintFormat(">>> [MICRO-RATCHET BE] BUY #%I64u locked to Breakeven at %.2f (+%d pts buffer)",
                           pos_ticket, target_be_sl, InpBEBufferPoints);
            }
         }
      }

      // --- TIER 2: CONTINUOUS RATCHET TRAILING ---
      if(gain_points >= (InpBETriggerPoints + InpTrailStepPoints))
      {
         double target_trail_sl = NormalizeDouble(bid - (InpTrailDistancePoints * _Point), _Digits);
         double step_diff = (target_trail_sl - current_sl) / _Point;

         if(step_diff >= InpTrailStepPoints && target_trail_sl > open_price)
         {
            if(m_trade.PositionModify(pos_ticket, target_trail_sl, current_tp))
            {
               PrintFormat(">>> [MICRO-RATCHET STEP] BUY #%I64u SL stepped to %.2f (Peak: %.2f)",
                           pos_ticket, target_trail_sl, m_highest_peak_price);
            }
         }
      }
   }
   // ===================================================================
   // SELL POSITION MICRO-RATCHET ENGINE
   // ===================================================================
   else if(pos_type == POSITION_TYPE_SELL)
   {
      double gain_points = (open_price - ask) / _Point;

      // Track lowest expansion price
      if(ask < m_lowest_peak_price || m_lowest_peak_price == 0.0)
         m_lowest_peak_price = ask;

      // --- TIER 1: FAST BREAKEVEN FLOOR (+InpBETriggerPoints) ---
      if(gain_points >= InpBETriggerPoints && !m_be_locked)
      {
         double target_be_sl = NormalizeDouble(open_price - (InpBEBufferPoints * _Point), _Digits);
         if(current_sl == 0.0 || current_sl > target_be_sl)
         {
            if(m_trade.PositionModify(pos_ticket, target_be_sl, current_tp))
            {
               m_be_locked = true;
               PrintFormat(">>> [MICRO-RATCHET BE] SELL #%I64u locked to Breakeven at %.2f (+%d pts buffer)",
                           pos_ticket, target_be_sl, InpBEBufferPoints);
            }
         }
      }

      // --- TIER 2: CONTINUOUS RATCHET TRAILING ---
      if(gain_points >= (InpBETriggerPoints + InpTrailStepPoints))
      {
         double target_trail_sl = NormalizeDouble(ask + (InpTrailDistancePoints * _Point), _Digits);
         double step_diff = (current_sl - target_trail_sl) / _Point;

         if(step_diff >= InpTrailStepPoints && target_trail_sl < open_price)
         {
            if(m_trade.PositionModify(pos_ticket, target_trail_sl, current_tp))
            {
               PrintFormat(">>> [MICRO-RATCHET STEP] SELL #%I64u SL stepped to %.2f (Trough: %.2f)",
                           pos_ticket, target_trail_sl, m_lowest_peak_price);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Helper: Select Active Position for this EA                       |
//+------------------------------------------------------------------+
bool SelectActivePosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Magic() == InpMagic && m_position.Symbol() == _Symbol)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Helper: Check if Any Active Position Exists                     |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   return SelectActivePosition();
}

//+------------------------------------------------------------------+
//| Helper: Locate Existing Pending Orders for this EA               |
//+------------------------------------------------------------------+
void LocateActivePendingOrders()
{
   m_buy_stop_ticket  = 0;
   m_sell_stop_ticket = 0;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(m_order.SelectByIndex(i))
      {
         if(m_order.Magic() == InpMagic && m_order.Symbol() == _Symbol)
         {
            ENUM_ORDER_TYPE type = m_order.OrderType();
            if(type == ORDER_TYPE_BUY_STOP)
            {
               m_buy_stop_ticket = m_order.Ticket();
               m_last_buy_stop_price = m_order.PriceOpen();
            }
            else if(type == ORDER_TYPE_SELL_STOP)
            {
               m_sell_stop_ticket = m_order.Ticket();
               m_last_sell_stop_price = m_order.PriceOpen();
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Helper: Purge All Pending Brackets for this EA                   |
//+------------------------------------------------------------------+
void PurgePendingBrackets(string reason)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(m_order.SelectByIndex(i))
      {
         if(m_order.Magic() == InpMagic && m_order.Symbol() == _Symbol)
         {
            ulong ticket = m_order.Ticket();
            if(m_trade.OrderDelete(ticket))
            {
               PrintFormat("[ORDER PURGE] Cancelled #%I64u. Reason: %s", ticket, reason);
            }
         }
      }
   }
   m_buy_stop_ticket      = 0;
   m_sell_stop_ticket     = 0;
   m_last_buy_stop_price  = 0.0;
   m_last_sell_stop_price = 0.0;
}

//+------------------------------------------------------------------+
//| Helper: Calculate Normalized Lot Sizing                          |
//+------------------------------------------------------------------+
double CalculateTradeLotSize()
{
   double lot = InpFixedLots;

   // Dynamic Risk Sizing if InpFixedLots is 0.0
   if(lot <= 0.0)
   {
      double equity = m_account.Equity();
      double risk_amount = equity * (InpRiskPercent / 100.0);
      double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

      if(tick_value > 0.0 && tick_size > 0.0 && InpInitialSLPoints > 0)
      {
         double points_loss_per_lot = (InpInitialSLPoints * _Point / tick_size) * tick_value;
         if(points_loss_per_lot > 0.0)
            lot = risk_amount / points_loss_per_lot;
      }
      if(lot <= 0.0)
         lot = 0.01;
   }

   // Clamp to broker lot limits
   double min_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   lot = MathMax(min_lot, MathMin(max_lot, lot));
   lot = MathFloor(lot / step_lot) * step_lot;

   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
//| Helper: Retrieve M1 ATR Value                                    |
//+------------------------------------------------------------------+
double GetCurrentM1ATR()
{
   if(m_atr_handle == INVALID_HANDLE)
      return 0.0;

   double atr_buf[1];
   if(CopyBuffer(m_atr_handle, 0, 1, 1, atr_buf) <= 0)
      return 0.0;

   return atr_buf[0];
}

//+------------------------------------------------------------------+
//| Helper: Synchronize State and Open Position on Startup           |
//+------------------------------------------------------------------+
void SyncCurrentPositionsAndOrders()
{
   if(HasOpenPosition())
   {
      m_state = STATE_IN_TRADE;
      m_active_pos_ticket = m_position.Ticket();
      m_be_locked = false;
      m_highest_peak_price = m_position.PriceOpen();
      m_lowest_peak_price  = m_position.PriceOpen();
   }
   else
   {
      m_state = STATE_HUNTING;
      LocateActivePendingOrders();
   }
}

//+------------------------------------------------------------------+
//| On-Chart Telemetry Dashboard HUD                                 |
//+------------------------------------------------------------------+
void UpdateOnChartHUD(long spread, string status_text)
{
   string hud = "";
   hud += "========================================================\n";
   hud += " INSTITUTIONAL DYNAMIC STRADDLE SCALPER (M1 GOLD)       \n";
   hud += "========================================================\n";
   hud += StringFormat(" State Engine       : %s\n", status_text);
   hud += StringFormat(" Magic Number       : %I64u\n", InpMagic);
   hud += StringFormat(" Live Spread        : %d pts (Max Allowed: %d pts)\n", spread, InpMaxSpreadPoints);
   hud += StringFormat(" M1 ATR (14)        : %.2f ($%.2f)\n", GetCurrentM1ATR(), GetCurrentM1ATR());
   hud += "--------------------------------------------------------\n";
   
   if(m_state == STATE_IN_TRADE)
   {
      hud += StringFormat(" Position Active    : Ticket #%I64u (%s)\n", 
                          m_position.Ticket(), EnumToString(m_position.PositionType()));
      hud += StringFormat(" Open Price         : %.2f | Current SL: %.2f\n", 
                          m_position.PriceOpen(), m_position.StopLoss());
      hud += StringFormat(" Breakeven Status   : %s\n", m_be_locked ? "LOCKED (+BE Buffer)" : "ARMING");
      hud += StringFormat(" Floating Profit    : $%.2f\n", m_position.Profit());
   }
   else if(m_state == STATE_COOLDOWN)
   {
      long rem_sec = (long)(m_cooldown_expiry - TimeCurrent());
      hud += StringFormat(" Cooldown Timer     : %d seconds remaining\n", MathMax(0, rem_sec));
   }
   else // STATE_HUNTING
   {
      hud += StringFormat(" Buy Stop Bracket   : %s (%.2f)\n", 
                          (m_buy_stop_ticket > 0) ? StringFormat("#%I64u", m_buy_stop_ticket) : "Placing...", m_last_buy_stop_price);
      hud += StringFormat(" Sell Stop Bracket  : %s (%.2f)\n", 
                          (m_sell_stop_ticket > 0) ? StringFormat("#%I64u", m_sell_stop_ticket) : "Placing...", m_last_sell_stop_price);
      hud += StringFormat(" Reposition Step    : %d points rate-limit\n", InpRepositionStepPoints);
   }
   hud += "========================================================\n";

   Comment(hud);
}
//+------------------------------------------------------------------+
