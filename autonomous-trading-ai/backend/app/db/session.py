import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config.settings import settings
import logging

logger = logging.getLogger(__name__)

Base = declarative_base()

def get_engine():
    db_url = settings.DATABASE_URL
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    default_sqlite_path = os.path.join(backend_dir, "trading.db").replace("\\", "/")
    
    try:
        if db_url.startswith("postgresql"):
            # Try connecting to PostgreSQL
            engine = create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
            with engine.connect() as conn:
                logger.info("Successfully connected to PostgreSQL database.")
            return engine
    except Exception as e:
        logger.warning(f"PostgreSQL connection failed ({e}). Falling back to local SQLite database.")
        db_url = f"sqlite:///{default_sqlite_path}"
    
    # If SQLite relative path was specified in settings, anchor to backend directory
    if "sqlite" in db_url and ("./trading.db" in db_url or db_url == "sqlite:///trading.db"):
        db_url = f"sqlite:///{default_sqlite_path}"

    return create_engine(
        db_url,
        connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        pool_pre_ping=True
    )

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
