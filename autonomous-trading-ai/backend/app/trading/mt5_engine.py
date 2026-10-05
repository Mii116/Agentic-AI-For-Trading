import logging
import time
import MetaTrader5 as mt5
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from app.db.session import SessionLocal
from app.models.strategy import TradingHypothesis
from app.models.trading import Order, Position, AccountBalance, TradeJournal
from app.config.settings import settings
from app.risk.stop_policy import MIN_STOP_DISTANCE, enforce_min_stop

logger = logging.getLogger(__name__)

class MT5ExecutionEngine:
    def __init__(self):
        self.mode = "MT5_DEMO" if settings.MT5_MODE.lower() == "demo" else "MT5_LIVE"
        self.enabled = settings.MT5_ENABLED
        
        if not self.enabled:
            logger.warning("MT5ExecutionEngine initialized but MT5_ENABLED is false.")
            return

        # Initialize MT5 connection using explicit path
        terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
        if not mt5.initialize(path=terminal_path):
            logger.error(f"Failed to initialize MT5: {mt5.last_error()}")
            return
            
        logger.info("MT5 initialized successfully.")
        
        # Bypass login since user is manually logged into the terminal
        account_info = mt5.account_info()
        if account_info:
            logger.info(f"Attached to currently active MT5 account #{account_info.login}")
        else:
            logger.error("Terminal is open but no account is logged in.")

    def sync_account_balance(self):
        """Fetches the real MT5 account balance and updates the database."""
        if not self.enabled:
            return
            
        account_info = mt5.account_info()
        if not account_info:
            logger.error(f"Failed to retrieve MT5 account info: {mt5.last_error()}")
            return
            
        db = SessionLocal()
        try:
            account = db.query(AccountBalance).filter_by(mode=self.mode).first()
            if not account:
                account = AccountBalance(mode=self.mode)
                db.add(account)
                
            account.cash_balance = account_info.balance
            account.equity = account_info.equity
            account.used_margin = account_info.margin
            account.free_margin = account_info.margin_free
            
            db.commit()
            logger.info(f"Synced MT5 Account Balance: Equity ${account.equity}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to sync MT5 balance to DB: {e}")
        finally:
            db.close()

    def sync_positions(self):
        """Fetches active positions from MT5 and syncs them to local DB."""
        if not self.enabled:
            return
            
        mt5_positions = mt5.positions_get()
        if mt5_positions is None:
            return
            
        db = SessionLocal()
        try:
            for p in mt5_positions:
                side = "LONG" if p.type == mt5.ORDER_TYPE_BUY else "SHORT"
                existing = db.query(Position).filter_by(
                    symbol=p.symbol,
                    side=side,
                    mode=self.mode,
                    is_open=True
                ).first()
                
                if existing:
                    existing.current_price = p.price_current
                    existing.unrealized_pnl = p.profit
                    existing.stop_loss = p.sl
                    existing.take_profit = p.tp
                else:
                    new_pos = Position(
                        symbol=p.symbol,
                        side=side,
                        quantity=p.volume,
                        entry_price=p.price_open,
                        current_price=p.price_current,
                        unrealized_pnl=p.profit,
                        stop_loss=p.sl,
                        take_profit=p.tp,
                        is_open=True,
                        mode=self.mode,
                        opened_at=datetime.fromtimestamp(p.time)
                    )
                    db.add(new_pos)
            db.commit()
            logger.info(f"Synced {len(mt5_positions)} open positions from MT5.")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to sync MT5 positions: {e}")
        finally:
            db.close()

    def map_symbol_to_mt5(self, symbol: str) -> str:
        """Map internal ETF/symbol names to MT5 symbols."""
        mapping = {
            "GLD": "XAUUSD",
            "IBIT": "BTCUSD"
        }
        return mapping.get(symbol, symbol)

    def process_new_hypotheses(self):
        """Finds PROPOSED hypotheses and executes them as real MT5 trades."""
        if not self.enabled:
            return

        db = SessionLocal()
        try:
            hypotheses = db.query(TradingHypothesis).filter_by(status="PROPOSED").all()
            if not hypotheses:
                return

            for hyp in hypotheses:
                logger.info(f"Processing Hypothesis #{hyp.id} for {hyp.symbol} on MT5")
                
                # Check confidence threshold
                if hyp.confidence < 0.6:
                    logger.warning(f"Confidence too low ({hyp.confidence}). Rejecting.")
                    hyp.status = "REJECTED"
                    continue
                
                mt5_symbol = self.map_symbol_to_mt5(hyp.symbol)

                # If direction is CLOSE, close all positions for this symbol
                if hyp.direction == "CLOSE":
                    logger.info(f"AI requested CLOSE for {mt5_symbol}. Closing active positions...")
                    self.close_all_positions(symbol=mt5_symbol)
                    hyp.status = "EXECUTED"
                    continue

                # Close any existing opposite positions (e.g. close BUY if new signal is SELL)
                open_positions = mt5.positions_get(symbol=mt5_symbol)
                opposite_type = mt5.ORDER_TYPE_SELL if hyp.direction == "BUY" else mt5.ORDER_TYPE_BUY
                target_type = mt5.ORDER_TYPE_BUY if hyp.direction == "BUY" else mt5.ORDER_TYPE_SELL

                if open_positions:
                    for p in open_positions:
                        if p.type == opposite_type:
                            logger.info(f"Signal reversal detected! Closing opposite position #{p.ticket} on {mt5_symbol}...")
                            self.close_position(p.ticket)
                    
                    # Refresh open positions after closing opposite
                    open_positions = mt5.positions_get(symbol=mt5_symbol)
                    if open_positions:
                        matching = [p for p in open_positions if p.type == target_type]
                        if matching:
                            logger.info(f"Already have an open {hyp.direction} position on {mt5_symbol}. Skipping duplicate entry.")
                            hyp.status = "SKIPPED_EXISTING"
                            continue
                
                # Ensure symbol is available in MT5
                symbol_info = mt5.symbol_info(mt5_symbol)
                if symbol_info is None:
                    logger.error(f"Symbol {mt5_symbol} not found in MT5.")
                    hyp.status = "REJECTED"
                    continue
                
                if not symbol_info.visible:
                    logger.info(f"Symbol {mt5_symbol} is not visible, trying to switch on")
                    if not mt5.symbol_select(mt5_symbol, True):
                        logger.error(f"symbol_select({mt5_symbol}) failed")
                        hyp.status = "REJECTED"
                        continue

                # Calculate order size (volume) based on risk
                account_info = mt5.account_info()
                if not account_info:
                    continue
                    
                # Standard risk: 5% of equity, multiplied by leverage
                capital_to_risk = account_info.equity * settings.MAX_POSITION_SIZE_PCT
                max_notional = capital_to_risk * settings.LEVERAGE
                
                # Get current tick for accurate pricing
                tick = mt5.symbol_info_tick(mt5_symbol)
                price = tick.ask if hyp.direction == "BUY" else tick.bid
                
                # Calculate volume (MT5 usually uses lots, where 1 lot = contract size)
                # For XAUUSD, 1 lot = 100 oz. Let's just use minimal volume for safety first.
                volume = 0.01  # Hardcoded safety limit for MVP
                
                order_type = mt5.ORDER_TYPE_BUY if hyp.direction == "BUY" else mt5.ORDER_TYPE_SELL
                
                # Determine correct filling mode for this specific broker
                filling_type = mt5.ORDER_FILLING_FOK
                if symbol_info.filling_mode == 1:
                    filling_type = mt5.ORDER_FILLING_FOK
                elif symbol_info.filling_mode == 2:
                    filling_type = mt5.ORDER_FILLING_IOC
                else:
                    filling_type = mt5.ORDER_FILLING_RETURN # Often required for Forex/Gold brokers
                
                # Sanitize Stop Loss & Take Profit against broker stops level
                sl = float(hyp.suggested_stop_loss) if hyp.suggested_stop_loss else 0.0
                tp = float(hyp.suggested_take_profit) if hyp.suggested_take_profit else 0.0

                point = symbol_info.point or 0.01
                min_stop_distance = max((symbol_info.trade_stops_level or 0) * point, point * 10)

                if hyp.direction == "BUY":
                    if sl > 0 and sl >= (price - min_stop_distance):
                        logger.warning(f"Invalid SL for BUY ({sl} >= {price - min_stop_distance}). Resetting SL to 0.0")
                        sl = 0.0
                    if tp > 0 and tp <= (price + min_stop_distance):
                        logger.warning(f"Invalid TP for BUY ({tp} <= {price + min_stop_distance}). Resetting TP to 0.0")
                        tp = 0.0
                elif hyp.direction == "SELL":
                    if sl > 0 and sl <= (price + min_stop_distance):
                        logger.warning(f"Invalid SL for SELL ({sl} <= {price + min_stop_distance}). Resetting SL to 0.0")
                        sl = 0.0
                    if tp > 0 and tp >= (price - min_stop_distance):
                        logger.warning(f"Invalid TP for SELL ({tp} >= {price - min_stop_distance}). Resetting TP to 0.0")
                        tp = 0.0
                
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": mt5_symbol,
                    "volume": volume,
                    "type": order_type,
                    "price": price,
                    "sl": sl,
                    "tp": tp,
                    "deviation": 25,
                    "magic": 234000,
                    "comment": f"Agent Trade {hyp.id}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling_type,
                }
                
                logger.info(f"Sending MT5 Order: {request}")
                
                if settings.LIVE_TRADING_ENABLED:
                    result = mt5.order_send(request)
                    if result.retcode != mt5.TRADE_RETCODE_DONE:
                        logger.error(f"MT5 Order Failed: retcode={result.retcode}")
                        hyp.status = "REJECTED"
                    else:
                        logger.info(f"MT5 Order Success! Ticket: {result.order}")
                        hyp.status = "EXECUTED"
                        
                        # Save to DB
                        order = Order(
                            symbol=mt5_symbol,
                            side=hyp.direction,
                            quantity=volume,
                            price=price,
                            status="FILLED",
                            mode=self.mode
                        )
                        db.add(order)
                else:
                    logger.info("LIVE_TRADING_ENABLED is False. Skipping actual MT5 execution.")
                    hyp.status = "REJECTED"

            db.commit()
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error processing MT5 trades: {e}")
        finally:
            db.close()

    def execute_custom_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "Agent Trade",
        proposal: Any = None
    ) -> bool:
        """
        Executes a targeted order on MT5 with explicit volume, SL, and TP.
        Called directly by ChiefRiskArbiter after validating all risk and market filters.
        """
        if not self.enabled:
            logger.warning("MT5ExecutionEngine is disabled.")
            return False

        mt5_symbol = self.map_symbol_to_mt5(symbol)
        symbol_info = mt5.symbol_info(mt5_symbol)
        if not symbol_info:
            logger.error(f"Symbol {mt5_symbol} not found in MT5.")
            return False

        if not symbol_info.visible:
            mt5.symbol_select(mt5_symbol, True)

        tick = mt5.symbol_info_tick(mt5_symbol)
        if not tick:
            logger.error(f"Cannot get tick for {mt5_symbol}")
            return False

        order_type = mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side.upper() == "BUY" else tick.bid

        # Determine filling mode
        filling_type = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode == 1:
            filling_type = mt5.ORDER_FILLING_FOK
        elif symbol_info.filling_mode == 2:
            filling_type = mt5.ORDER_FILLING_IOC
        else:
            filling_type = mt5.ORDER_FILLING_RETURN

        point = symbol_info.point or 0.01
        min_stop_distance = max((symbol_info.trade_stops_level or 0) * point, point * 10, MIN_STOP_DISTANCE)

        sl = float(stop_loss) if stop_loss else 0.0
        tp = float(take_profit) if take_profit else 0.0

        if side.upper() == "BUY":
            if sl > 0 and sl >= (price - min_stop_distance):
                logger.warning(f"Invalid SL for BUY ({sl} >= {price - min_stop_distance}). Resetting SL to 0.0")
                sl = 0.0
            if tp > 0 and tp <= (price + min_stop_distance):
                logger.warning(f"Invalid TP for BUY ({tp} <= {price + min_stop_distance}). Resetting TP to 0.0")
                tp = 0.0
        elif side.upper() == "SELL":
            if sl > 0 and sl <= (price + min_stop_distance):
                logger.warning(f"Invalid SL for SELL ({sl} <= {price + min_stop_distance}). Resetting SL to 0.0")
                sl = 0.0
            if tp > 0 and tp >= (price - min_stop_distance):
                logger.warning(f"Invalid TP for SELL ({tp} >= {price - min_stop_distance}). Resetting TP to 0.0")
                tp = 0.0

        magic_num = proposal.magic_number if proposal and hasattr(proposal, "magic_number") else 1001

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": mt5_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 25,
            "magic": magic_num,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }

        logger.info(f"Sending Institutional MT5 Order: {request}")

        if settings.LIVE_TRADING_ENABLED:
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"MT5 Order Failed: retcode={result.retcode} ({mt5.last_error()})")
                return False
            else:
                logger.info(f"MT5 Order Success! Ticket: {result.order}")
                db = SessionLocal()
                try:
                    order = Order(
                        symbol=mt5_symbol,
                        side=side.upper(),
                        quantity=float(volume),
                        price=price,
                        stop_loss=sl,
                        take_profit=tp,
                        status="FILLED",
                        mode=self.mode
                    )
                    db.add(order)

                    # Also record in TradeJournal for full auditing
                    from app.models.trading import TradeJournal
                    technique = comment
                    reason_text = "Institutional SMC Signal"
                    invalidation_text = f"Structural stop at {sl}"
                    if proposal:
                        technique = f"{proposal.agent_role} ({proposal.timeframe})"
                        reason_text = f"{proposal.thesis} | Confluences: {', '.join(proposal.supporting_confluences)}"
                        invalidation_text = proposal.invalidation_condition or invalidation_text

                    journal = TradeJournal(
                        ticket=result.order,
                        symbol=mt5_symbol,
                        side=side.upper(),
                        lot_size=float(volume),
                        entry_price=price,
                        stop_loss=sl,
                        take_profit=tp,
                        status="OPEN",
                        magic_number=magic_num,
                        technique_used=technique,
                        reason=reason_text,
                        invalidation_condition=invalidation_text,
                        opened_at=datetime.utcnow()
                    )
                    db.add(journal)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    logger.error(f"Error recording order to DB: {e}")
                finally:
                    db.close()
                return True
        else:
            logger.info("LIVE_TRADING_ENABLED is False. Skipping actual MT5 execution.")
            return False

    def execute_custom_limit_order(
        self,
        symbol: str,
        side: str,
        limit_price: float,
        volume: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "Limit Order",
        proposal: Any = None,
        ttl_minutes: int = 20
    ) -> bool:
        """
        Executes a targeted Pending Limit Order (ORDER_TYPE_BUY_LIMIT or ORDER_TYPE_SELL_LIMIT) on MT5.
        - Bullish setups: Place ORDER_TYPE_BUY_LIMIT at 50% equilibrium or top edge of M1/M5 FVG/demand block.
        - Bearish setups: Place ORDER_TYPE_SELL_LIMIT at 50% equilibrium or bottom edge of M1/M5 FVG/supply block.
        - Time-To-Live (TTL): 20-minute expiration timestamp (type_time = ORDER_TIME_SPECIFIED).
        """
        if not self.enabled:
            logger.warning("MT5ExecutionEngine is disabled.")
            return False

        mt5_symbol = self.map_symbol_to_mt5(symbol)
        symbol_info = mt5.symbol_info(mt5_symbol)
        if not symbol_info:
            logger.error(f"Symbol {mt5_symbol} not found in MT5.")
            return False

        if not symbol_info.visible:
            mt5.symbol_select(mt5_symbol, True)

        tick = mt5.symbol_info_tick(mt5_symbol)
        if not tick:
            logger.error(f"Cannot get tick for {mt5_symbol}")
            return False

        point = symbol_info.point or 0.01
        min_stop_distance = max((symbol_info.trade_stops_level or 0) * point, point * 10, MIN_STOP_DISTANCE)

        price = round(float(limit_price), 2)
        sl = round(float(stop_loss), 2) if stop_loss else 0.0
        tp = round(float(take_profit), 2) if take_profit else 0.0

        if side.upper() == "BUY":
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
            # BUY_LIMIT must be below current ask
            if price >= tick.ask:
                logger.warning(f"BUY_LIMIT price {price} is >= current ask {tick.ask}. Adjusting price to {round(tick.ask - 2 * point, 2)}")
                price = round(tick.ask - 2 * point, 2)
            if sl > 0 and sl >= (price - min_stop_distance):
                sl = round(price - min_stop_distance, 2)
            if tp > 0 and tp <= (price + min_stop_distance):
                tp = round(price + min_stop_distance, 2)

        elif side.upper() == "SELL":
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
            # SELL_LIMIT must be above current bid
            if price <= tick.bid:
                logger.warning(f"SELL_LIMIT price {price} is <= current bid {tick.bid}. Adjusting price to {round(tick.bid + 2 * point, 2)}")
                price = round(tick.bid + 2 * point, 2)
            if sl > 0 and sl <= (price + min_stop_distance):
                sl = round(price + min_stop_distance, 2)
            if tp > 0 and tp >= (price - min_stop_distance):
                tp = round(price - min_stop_distance, 2)
        else:
            logger.error(f"Unsupported side for limit order: {side}")
            return False

        # Determine filling mode
        filling_type = mt5.ORDER_FILLING_RETURN
        if symbol_info.filling_mode == 1:
            filling_type = mt5.ORDER_FILLING_FOK
        elif symbol_info.filling_mode == 2:
            filling_type = mt5.ORDER_FILLING_IOC

        magic_num = proposal.magic_number if proposal and hasattr(proposal, "magic_number") else 1001
        expiration_ts = int(time.time() + (ttl_minutes * 60))

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": mt5_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 25,
            "magic": magic_num,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": expiration_ts,
            "type_filling": filling_type,
        }

        logger.info(f"Sending MT5 Limit Order: {request}")

        if settings.LIVE_TRADING_ENABLED:
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                # Fallback to GTC if broker rejects ORDER_TIME_SPECIFIED
                logger.warning(f"MT5 Limit with ORDER_TIME_SPECIFIED failed (retcode={result.retcode}). Retrying with ORDER_TIME_GTC...")
                request["type_time"] = mt5.ORDER_TIME_GTC
                request.pop("expiration", None)
                result = mt5.order_send(request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"MT5 Limit Order Failed: retcode={result.retcode} ({mt5.last_error()})")
                return False
            else:
                logger.info(f"MT5 Limit Order Placed! Ticket: {result.order} at {price}")
                db = SessionLocal()
                try:
                    order = Order(
                        symbol=mt5_symbol,
                        side=side.upper(),
                        order_type="LIMIT",
                        quantity=float(volume),
                        price=price,
                        stop_loss=sl,
                        take_profit=tp,
                        status="PENDING",
                        mode=self.mode
                    )
                    db.add(order)

                    # Also record in TradeJournal for full auditing and learning
                    technique = comment
                    reason_text = "Pending Limit SMC Execution"
                    invalidation_text = f"Cancel if 5m CHoCH or SL {sl}"
                    if proposal:
                        technique = f"{getattr(proposal, 'setup_cluster', '') or proposal.agent_role} (LIMIT)"
                        reason_text = f"{proposal.thesis} | Cluster: {getattr(proposal, 'setup_cluster', 'M5_FVG_LIMIT')} | TTL: {ttl_minutes}m"
                        invalidation_text = proposal.invalidation_condition or invalidation_text

                    journal = TradeJournal(
                        ticket=result.order,
                        symbol=mt5_symbol,
                        side=side.upper(),
                        lot_size=float(volume),
                        entry_price=price,
                        stop_loss=sl,
                        take_profit=tp,
                        status="OPEN",
                        magic_number=magic_num,
                        technique_used=technique,
                        reason=reason_text,
                        invalidation_condition=invalidation_text,
                        opened_at=datetime.utcnow()
                    )
                    db.add(journal)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    logger.error(f"Error recording limit order to DB: {e}")
                finally:
                    db.close()
                return True
        else:
            logger.info("LIVE_TRADING_ENABLED is False. Skipping actual MT5 limit execution.")
            return False

    def cancel_pending_order(self, order_ticket: int, reason: str = "") -> bool:
        """Cancels an active pending order on MT5 (ORDER_CANCEL via TRADE_ACTION_REMOVE)."""
        if not self.enabled:
            return False

        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(order_ticket),
            "comment": f"Cancel: {reason[:20]}"
        }
        res = mt5.order_send(req)
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Successfully cancelled pending order #{order_ticket}. Reason: {reason}")
            db = SessionLocal()
            try:
                order_rec = db.query(Order).filter_by(id=order_ticket).first()
                if order_rec:
                    order_rec.status = "CANCELLED"
                journal_rec = db.query(TradeJournal).filter_by(ticket=order_ticket).first()
                if journal_rec and journal_rec.status == "OPEN":
                    journal_rec.status = "CANCELLED"
                    journal_rec.exit_reason = f"ORDER_CANCEL: {reason}"
                    journal_rec.closed_at = datetime.utcnow()
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Error updating cancelled order in DB: {e}")
            finally:
                db.close()
            return True
        else:
            logger.warning(f"Failed to cancel pending order #{order_ticket}: retcode={res.retcode} ({mt5.last_error()})")
            return False

    def manage_pending_limit_orders(
        self,
        symbol: str = "XAUUSD",
        m5_bars: Optional[List[Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Active Pending Limit Management:
        1. TTL Expiration: Automatically cancels any limit order older than 20 minutes (1200s).
        2. Active Invalidation: If price forms a Change of Character (CHoCH) on the 5m chart
           before tagging the limit order, immediately sends ORDER_CANCEL (TRADE_ACTION_REMOVE).
        """
        if not self.enabled or not mt5.initialize():
            return []

        mt5_symbol = self.map_symbol_to_mt5(symbol)
        pending_orders = mt5.orders_get(symbol=mt5_symbol)
        if not pending_orders:
            return []

        # Fresh M5 bars if not supplied
        if m5_bars is None or len(m5_bars) < 8:
            rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M5, 0, 30)
            if rates is not None and len(rates) > 0:
                m5_bars = [{
                    "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                    "close": float(r[4]), "volume": float(r[5])
                } for r in rates]

        cancelled_events = []
        tick = mt5.symbol_info_tick(mt5_symbol)
        current_time = tick.time if tick else time.time()

        from app.indicators.smc import SMCAnalyzer
        bos_data = SMCAnalyzer.detect_bos_choch(m5_bars, left_bars=2, right_bars=2) if m5_bars else {}
        choch = bos_data.get("choch")
        swing_highs = bos_data.get("swing_highs", [])
        swing_lows = bos_data.get("swing_lows", [])
        latest_close = float(m5_bars[-1]["close"] if isinstance(m5_bars[-1], dict) else m5_bars[-1].close) if m5_bars else 0.0

        for order in pending_orders:
            # Only manage BUY_LIMIT and SELL_LIMIT orders
            if order.type not in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT):
                continue

            order_ticket = order.ticket
            order_type = "BUY_LIMIT" if order.type == mt5.ORDER_TYPE_BUY_LIMIT else "SELL_LIMIT"
            order_age_sec = max(0, current_time - order.time_setup)

            should_cancel = False
            cancel_reason = ""

            # 1. TTL Check (20 minutes = 1200 seconds)
            if order_age_sec >= 1200:
                should_cancel = True
                cancel_reason = f"20-Minute TTL Expired ({order_age_sec:.0f}s elapsed)"

            # 2. Active Invalidation: 5m Change of Character (CHoCH) against setup before fill
            elif m5_bars and len(m5_bars) >= 8:
                if order_type == "BUY_LIMIT":
                    # Pending Buy Limit invalidated if Bearish CHoCH occurs (clean close below recent swing low)
                    has_bearish_choch = (choch and "BEARISH" in choch.get("type", ""))
                    if not has_bearish_choch and swing_lows:
                        recent_low = swing_lows[-1]["price"]
                        if latest_close < recent_low:
                            has_bearish_choch = True
                    if has_bearish_choch:
                        should_cancel = True
                        cancel_reason = f"Active Invalidation: 5m Bearish CHoCH detected before fill (Close={latest_close:.2f})"

                elif order_type == "SELL_LIMIT":
                    # Pending Sell Limit invalidated if Bullish CHoCH occurs (clean close above recent swing high)
                    has_bullish_choch = (choch and "BULLISH" in choch.get("type", ""))
                    if not has_bullish_choch and swing_highs:
                        recent_high = swing_highs[-1]["price"]
                        if latest_close > recent_high:
                            has_bullish_choch = True
                    if has_bullish_choch:
                        should_cancel = True
                        cancel_reason = f"Active Invalidation: 5m Bullish CHoCH detected before fill (Close={latest_close:.2f})"

            if should_cancel:
                logger.warning(
                    f"[Limit Invalidation Engine] Cancelling {order_type} #{order_ticket} on {mt5_symbol}: {cancel_reason}"
                )
                success = self.cancel_pending_order(order_ticket, reason=cancel_reason)
                if success:
                    cancelled_events.append({
                        "order_ticket": order_ticket,
                        "type": order_type,
                        "reason": cancel_reason
                    })

        return cancelled_events

    def sync_closed_trades(self, symbol: str = "XAUUSD"):
        """
        Synchronizes closed trades from MT5 deal history into TradeJournal.
        Dual-Sided Continuous Learning: Runs AI Post-Mortem & Experience Learning on 100% of closed trades
        (both Wins and Losses) to formulation Winning Setup Signatures and Failure Anti-Patterns!
        """
        if not self.enabled:
            return

        from datetime import timedelta
        from app.models.trading import TradeJournal
        db = SessionLocal()
        try:
            open_journals = db.query(TradeJournal).filter_by(status="OPEN").all()
            if not open_journals:
                return

            now = datetime.now()
            from_time = now - timedelta(days=2)
            deals = mt5.history_deals_get(from_time, now)
            if deals is None:
                return

            active_positions = mt5.positions_get(symbol=symbol) or []
            active_tickets = {p.ticket for p in active_positions}

            for journal in open_journals:
                if journal.ticket in active_tickets:
                    continue

                # Ticket is no longer active in open positions, search for exit deal
                pos_deals = mt5.history_deals_get(position=journal.ticket)
                closing_deal = None
                if pos_deals:
                    for deal in pos_deals:
                        if deal.entry == mt5.DEAL_ENTRY_OUT:
                            closing_deal = deal
                            break

                if closing_deal:
                    pnl = float(closing_deal.profit)
                    exit_price = float(closing_deal.price)
                    comment = str(closing_deal.comment).lower()

                    if "tp" in comment:
                        exit_reason = "TAKE_PROFIT"
                    elif "sl" in comment:
                        exit_reason = "STOP_LOSS"
                    else:
                        exit_reason = journal.exit_reason or "MARKET_CLOSE"

                    journal.exit_price = exit_price
                    journal.realized_pnl = pnl
                    journal.closed_at = datetime.fromtimestamp(closing_deal.time)
                    journal.exit_reason = exit_reason
                    journal.status = "PASS" if pnl > 0 else ("FAIL" if pnl < -5.0 else "BREAKEVEN")
                    db.commit()

                    logger.info(f"Synced closed trade #{journal.ticket}: PnL=${pnl:.2f}, Outcome={journal.status}")

                    # Notify MarketCooldownManager for 20m $3 zone debounce & 15m scalper cooldown
                    try:
                        from app.trading.cooldown_manager import MarketCooldownManager
                        MarketCooldownManager.record_market_exit(
                            symbol=journal.symbol,
                            direction=journal.side,
                            exit_price=exit_price,
                            magic_number=journal.magic_number or 1001,
                            ticket=journal.ticket
                        )
                    except Exception as cde:
                        logger.warning(f"Cooldown manager recording error: {cde}")

                    # Dual-Sided Continuous Learning Engine: Reflect on 100% of closed trades
                    try:
                        from app.agent.post_mortem import PostMortemEngine
                        pm = PostMortemEngine()
                        pm.analyze_closed_trade(journal.id)
                    except Exception as pme:
                        logger.error(f"Error triggering post-mortem for #{journal.ticket}: {pme}")

        except Exception as e:
            db.rollback()
            logger.error(f"Error syncing closed trades: {e}")
        finally:
            db.close()

    def manage_breakeven_trailing(self, symbol: str = "XAUUSD"):
        """
        Breakeven Trailing: When any position reaches 1:1 Risk-to-Reward,
        automatically adjust Stop Loss to EntryPrice + SpreadBuffer.
        """
        if not self.enabled:
            return

        mt5_symbol = self.map_symbol_to_mt5(symbol)
        positions = mt5.positions_get(symbol=mt5_symbol)
        if not positions:
            return

        symbol_info = mt5.symbol_info(mt5_symbol)
        if not symbol_info:
            return
        point = symbol_info.point or 0.01
        spread_buffer = 15.0 * point

        db = SessionLocal()
        try:
            for pos in positions:
                entry_price = float(pos.price_open)
                current_price = float(pos.price_current)
                current_sl = float(pos.sl)
                side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"

                db_pos = db.query(Position).filter_by(
                    symbol=mt5_symbol,
                    side="LONG" if side == "BUY" else "SHORT",
                    mode=self.mode,
                    is_open=True
                ).first()
                initial_sl = float(db_pos.stop_loss) if (db_pos and db_pos.stop_loss) else current_sl

                # 1:1 R:R check for BUY
                if side == "BUY" and initial_sl > 0:
                    risk_distance = entry_price - initial_sl
                    if risk_distance > 0:
                        gain = current_price - entry_price
                        if gain >= risk_distance:
                            target_be_sl = round(entry_price + spread_buffer, 2)
                            if current_sl < target_be_sl:
                                logger.info(
                                    f"[Breakeven Trailing] Position #{pos.ticket} (BUY) hit 1:1 R:R! "
                                    f"Adjusting SL from {current_sl} to {target_be_sl} (Entry: {entry_price} + Buffer: {spread_buffer:.2f})"
                                )
                                req = {
                                    "action": mt5.TRADE_ACTION_SLTP,
                                    "position": pos.ticket,
                                    "symbol": pos.symbol,
                                    "sl": target_be_sl,
                                    "tp": pos.tp
                                }
                                res = mt5.order_send(req)
                                if res.retcode == mt5.TRADE_RETCODE_DONE:
                                    logger.info(f"SL successfully updated to breakeven for #{pos.ticket}")
                                else:
                                    logger.warning(f"Failed to move SL to breakeven: {res.retcode}")

                # 1:1 R:R check for SELL
                elif side == "SELL" and initial_sl > 0:
                    risk_distance = initial_sl - entry_price
                    if risk_distance > 0:
                        gain = entry_price - current_price
                        if gain >= risk_distance:
                            target_be_sl = round(entry_price - spread_buffer, 2)
                            if current_sl == 0.0 or current_sl > target_be_sl:
                                logger.info(
                                    f"[Breakeven Trailing] Position #{pos.ticket} (SELL) hit 1:1 R:R! "
                                    f"Adjusting SL from {current_sl} to {target_be_sl} (Entry: {entry_price} - Buffer: {spread_buffer:.2f})"
                                )
                                req = {
                                    "action": mt5.TRADE_ACTION_SLTP,
                                    "position": pos.ticket,
                                    "symbol": pos.symbol,
                                    "sl": target_be_sl,
                                    "tp": pos.tp
                                }
                                res = mt5.order_send(req)
                                if res.retcode == mt5.TRADE_RETCODE_DONE:
                                    logger.info(f"SL successfully updated to breakeven for #{pos.ticket}")
                                else:
                                    logger.warning(f"Failed to move SL to breakeven: {res.retcode}")

        except Exception as e:
            logger.error(f"Error managing breakeven trailing: {e}")
        finally:
            db.close()
            
    def close_position(self, ticket: int) -> bool:
        """Closes a specific MT5 position by ticket."""
        if not self.enabled:
            return False
            
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning(f"Position {ticket} not found in MT5 to close.")
            return False
            
        pos = positions[0]
        symbol_info = mt5.symbol_info(pos.symbol)
        if not symbol_info:
            logger.error(f"Cannot get symbol info for {pos.symbol}")
            return False
            
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            logger.error(f"Cannot get tick for {pos.symbol}")
            return False
            
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        
        # Determine filling type
        filling_type = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode == 1:
            filling_type = mt5.ORDER_FILLING_FOK
        elif symbol_info.filling_mode == 2:
            filling_type = mt5.ORDER_FILLING_IOC
        else:
            filling_type = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "price": price,
            "deviation": 25,
            "magic": pos.magic,
            "comment": f"Close #{pos.ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Successfully closed MT5 position #{ticket} at {price}")
            return True
        else:
            logger.error(f"Failed to close MT5 position #{ticket}: retcode={result.retcode}")
            return False

    def partial_close_position(self, ticket: int, close_volume: float) -> bool:
        """Closes a specific portion (e.g. 50%) of an active MT5 position."""
        if not self.enabled:
            return False

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning(f"Position {ticket} not found in MT5 for partial close.")
            return False

        pos = positions[0]
        symbol_info = mt5.symbol_info(pos.symbol)
        if not symbol_info:
            return False

        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return False

        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        filling_type = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode == 2:
            filling_type = mt5.ORDER_FILLING_IOC
        elif symbol_info.filling_mode != 1:
            filling_type = mt5.ORDER_FILLING_RETURN

        step = symbol_info.volume_step or 0.01
        norm_vol = max(symbol_info.volume_min, round(round(close_volume / step) * step, 2))
        if norm_vol >= pos.volume:
            norm_vol = round(pos.volume * 0.5, 2)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": float(norm_vol),
            "type": order_type,
            "price": price,
            "deviation": 25,
            "magic": pos.magic,
            "comment": f"Partial 50% #{pos.ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Successfully executed 50% partial close for #{ticket} ({norm_vol} lots at {price})")
            return True
        else:
            logger.error(f"Failed partial close for #{ticket}: retcode={result.retcode} ({mt5.last_error()})")
            return False

    def modify_position_sl(self, ticket: int, new_sl: float) -> bool:
        """Updates the Stop Loss on an active MT5 position."""
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "sl": float(round(new_sl, 2)),
            "tp": float(pos.tp)
        }
        res = mt5.order_send(req)
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Successfully updated SL on #{ticket} to {new_sl:.2f}")
            return True
        else:
            logger.warning(f"Failed to update SL on #{ticket}: retcode={res.retcode}")
            return False

    def check_shared_margin_guard(self) -> Tuple[bool, float, str]:
        """Shared Margin Guard: Verifies cumulative utilized margin does not exceed 20% of equity."""
        account_info = mt5.account_info()
        if not account_info or account_info.equity <= 0:
            return False, 0.0, "Cannot retrieve MT5 account info"
        margin_pct = (account_info.margin / account_info.equity) * 100.0
        if margin_pct > 20.0:
            msg = f"SHARED MARGIN GUARD BLOCKED: Cumulative margin utilization {margin_pct:.1f}% exceeds 20.0% ceiling."
            logger.warning(msg)
            return False, margin_pct, msg
        return True, margin_pct, f"Margin utilization normal ({margin_pct:.1f}% <= 20.0%)"

    def close_all_positions(self, symbol: Optional[str] = None):
        """Closes all open positions or all positions for a specific symbol."""
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if positions:
            for p in positions:
                self.close_position(p.ticket)

    def shutdown(self):
        mt5.shutdown()
