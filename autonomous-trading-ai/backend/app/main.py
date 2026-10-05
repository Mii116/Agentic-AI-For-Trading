import os
from fastapi import FastAPI, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import get_db
from app.api.dashboard import router as dashboard_router
from app.api.trading_api import router as trading_router

app = FastAPI(
    title="Autonomous Trading AI API",
    description="Institutional API for Multi-Trader Execution, Risk Controls, Portfolio Management & Telemetry",
    version="0.3.0",
)

# Include Dashboard & Institutional Trading API Routers
app.include_router(dashboard_router)
app.include_router(trading_router)

# Resolve static directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
@app.get("/dashboard")
def serve_dashboard():
    """Serves the institutional trading dashboard and trade journal."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Autonomous Trading AI Dashboard"}

class HealthResponse(BaseModel):
    status: str
    database: str
    mode: str

@app.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
        
    return {"status": "ok", "database": db_status, "mode": "MT5_LIVE"}
