import os
import time
import logging
try:
    import psutil
except ImportError:
    psutil = None
from typing import Dict, Any
from datetime import datetime, timezone
import MetaTrader5 as mt5

from app.db.session import SessionLocal
from app.config.settings import settings

logger = logging.getLogger(__name__)

class SystemHealthMonitor:
    """
    Institutional System Health & Telemetry Monitor:
    - MT5 broker connection latency and ping
    - Real-time tick latency and data stream health
    - Database responsiveness
    - Host resource utilization (CPU, Memory, Process uptime)
    """

    def __init__(self):
        self.start_time = time.time()

    def check_health(self) -> Dict[str, Any]:
        """Performs a comprehensive health check across all system subsystems."""
        # 1. Host Resources
        uptime_sec = round(time.time() - self.start_time, 1)
        if psutil:
            try:
                process = psutil.Process(os.getpid())
                cpu_pct = process.cpu_percent(interval=0.1)
                mem_mb = round(process.memory_info().rss / (1024 * 1024), 1)
                sys_mem_pct = psutil.virtual_memory().percent
            except Exception:
                cpu_pct, mem_mb, sys_mem_pct = 0.0, 0.0, 0.0
        else:
            cpu_pct, mem_mb, sys_mem_pct = 0.0, 0.0, 0.0

        # 2. Database Connectivity
        db_status = "HEALTHY"
        db_latency_ms = 0.0
        try:
            t0 = time.time()
            db = SessionLocal()
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
            db.close()
            db_latency_ms = round((time.time() - t0) * 1000, 2)
        except Exception as e:
            db_status = f"ERROR: {e}"

        # 3. MT5 Broker Stream
        mt5_status = "CONNECTED" if mt5.initialize() else "DISCONNECTED"
        mt5_ping_ms = 0.0
        tick_delay_ms = 0.0

        if mt5_status == "CONNECTED":
            acc = mt5.account_info()
            tick = mt5.symbol_info_tick("XAUUSD")
            if tick:
                now_epoch = time.time()
                tick_delay_ms = max(0.0, round((now_epoch - tick.time) * 1000, 1))

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "OPERATIONAL" if mt5_status == "CONNECTED" and db_status == "HEALTHY" else "DEGRADED",
            "uptime_seconds": uptime_sec,
            "subsystems": {
                "mt5_broker": {
                    "status": mt5_status,
                    "account": acc.login if acc else None,
                    "server": acc.server if acc else None,
                    "tick_delay_ms": tick_delay_ms
                },
                "database": {
                    "status": db_status,
                    "latency_ms": db_latency_ms
                },
                "host_resources": {
                    "cpu_percent": cpu_pct,
                    "memory_mb": round(mem_mb, 1),
                    "total_system_memory_pct": sys_mem_pct
                }
            }
        }
