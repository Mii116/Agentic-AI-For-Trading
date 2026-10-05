import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class AlertManager:
    """
    Central Alert Management & Dispatcher:
    - Captures critical operational events: Circuit breaker trips, Margin warnings, Spread spikes, Emergency exits.
    - Stores recent alerts in an in-memory audit log for real-time dashboard notification.
    """

    _alerts: List[Dict[str, Any]] = []
    MAX_ALERTS = 100

    @classmethod
    def emit_alert(cls, level: str, title: str, message: str, metadata: Dict[str, Any] = None):
        """
        Emits an institutional alert.
        Level: 'INFO', 'WARNING', 'CRITICAL', 'EMERGENCY'
        """
        alert = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "title": title,
            "message": message,
            "metadata": metadata or {}
        }
        cls._alerts.append(alert)
        if len(cls._alerts) > cls.MAX_ALERTS:
            cls._alerts.pop(0)

        log_msg = f"[ALERT - {level.upper()}] {title}: {message}"
        if level.upper() in ["CRITICAL", "EMERGENCY"]:
            logger.critical(log_msg)
        elif level.upper() == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

    @classmethod
    def get_recent_alerts(cls, limit: int = 20) -> List[Dict[str, Any]]:
        return list(reversed(cls._alerts[-limit:]))
