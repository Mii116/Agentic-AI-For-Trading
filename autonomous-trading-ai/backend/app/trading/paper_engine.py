import logging
from typing import List
from datetime import datetime
from app.db.session import SessionLocal
from app.models.strategy import TradingHypothesis
from app.models.trading import Order, Position, AccountBalance
from app.config.settings import settings

logger = logging.getLogger(__name__)

class PaperTradingEngine:
    def __init__(self):
        self.mode = "PAPER"

    def process_new_hypotheses(self):
        """Finds PROPOSED hypotheses and executes them against the paper account."""
        db = SessionLocal()
        try:
            hypotheses = db.query(TradingHypothesis).filter_by(status="PROPOSED").all()
            if not hypotheses:
                return

            # Ensure we have an account
            account = db.query(AccountBalance).filter_by(mode=self.mode).first()
            if not account:
                account = AccountBalance(mode=self.mode, cash_balance=settings.DEFAULT_CAPITAL, equity=settings.DEFAULT_CAPITAL, free_margin=settings.DEFAULT_CAPITAL)
                db.add(account)

            for hyp in hypotheses:
                logger.info(f"Processing Hypothesis #{hyp.id} for {hyp.symbol}")
                
                # Check confidence threshold
                if hyp.confidence < 0.6:
                    logger.warning(f"Confidence too low ({hyp.confidence}). Rejecting.")
                    hyp.status = "REJECTED"
                    continue
                
                # Calculate simple position size
                capital_to_risk = account.equity * settings.MAX_POSITION_SIZE_PCT
                
                if hyp.entry_price_estimate and hyp.entry_price_estimate > 0:
                    max_notional = capital_to_risk * settings.LEVERAGE
                    quantity = round(max_notional / hyp.entry_price_estimate, 4)
                else:
                    logger.error("Entry price estimate is zero or missing.")
                    hyp.status = "REJECTED"
                    continue

                if quantity <= 0:
                    logger.warning(f"Calculated quantity is <= 0 for {hyp.symbol}")
                    hyp.status = "REJECTED"
                    continue

                # Deduct margin
                required_margin = (quantity * hyp.entry_price_estimate) / settings.LEVERAGE
                if account.free_margin < required_margin:
                    logger.warning(f"Insufficient funds for {hyp.symbol}. Required Margin: {required_margin}, Available: {account.free_margin}")
                    hyp.status = "REJECTED"
                    continue

                # Create Order
                order = Order(
                    symbol=hyp.symbol,
                    side=hyp.direction,
                    order_type="MARKET",
                    quantity=quantity,
                    price=hyp.entry_price_estimate,
                    stop_loss=hyp.suggested_stop_loss,
                    take_profit=hyp.suggested_take_profit,
                    status="FILLED",
                    mode=self.mode
                )
                db.add(order)
                
                # Create Position
                position = Position(
                    symbol=hyp.symbol,
                    side="LONG" if hyp.direction == "BUY" else "SHORT",
                    quantity=quantity,
                    entry_price=hyp.entry_price_estimate,
                    current_price=hyp.entry_price_estimate,
                    stop_loss=hyp.suggested_stop_loss,
                    take_profit=hyp.suggested_take_profit,
                    mode=self.mode
                )
                db.add(position)

                # Update Account
                account.cash_balance -= required_margin
                account.used_margin += required_margin
                account.free_margin = account.equity - account.used_margin
                
                # Update Hypothesis status
                hyp.status = "EXECUTED"

                logger.info(f"Successfully PAPER TRADED {quantity} of {hyp.symbol} at {hyp.entry_price_estimate}")
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error processing paper trades: {e}")
        finally:
            db.close()
