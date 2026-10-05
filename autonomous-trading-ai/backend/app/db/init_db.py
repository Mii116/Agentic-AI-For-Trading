import logging
from app.db.session import engine, Base
from app.models import market_data, strategy, trading, audit

logger = logging.getLogger(__name__)

def init_db():
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
