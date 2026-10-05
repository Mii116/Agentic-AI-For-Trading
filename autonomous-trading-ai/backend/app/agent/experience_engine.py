import os
import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.trading import TradeJournal

logger = logging.getLogger(__name__)

EXPERIENCE_MATRIX_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "experience_matrix.json")
)

class ExperienceEngine:
    """
    Dual-Sided Continuous Learning & Experience Matrix Engine:
    - Maintains a persistent experience matrix of setup clusters (experience_matrix.json).
    - Tracks winning setup signatures and failure anti-patterns across 100% of closed trades.
    - Dynamically updates confidence score = Successful Trades / Total Attempts.
    - Gating rule:
        * Confidence >= 75%: Immediate approval with standard dynamic sizing.
        * Confidence < 60%: Requires higher-timeframe (H1) directional confirmation before permitting limit order.
    """

    DEFAULT_CLUSTERS = {
        "M5_FVG_LIMIT": {
            "total_attempts": 10,
            "successful_trades": 7,
            "failed_trades": 3,
            "confidence_score": 0.70,
            "winning_signatures": [
                "London volatility window entry at 50% FVG equilibrium with clean session liquidity sweep confirmation.",
                "Tight spread (< 25 pts) entry during NY open with volume expansion in direction of trend."
            ],
            "failure_anti_patterns": [
                "Entering M5 FVG during low-volume Asian consolidation without displacement.",
                "Front-running an unconfirmed sweep directly into higher-timeframe order block supply."
            ],
            "last_updated": datetime.now(timezone.utc).isoformat()
        },
        "M1_SWEEP_RETEST": {
            "total_attempts": 8,
            "successful_trades": 5,
            "failed_trades": 3,
            "confidence_score": 0.625,
            "winning_signatures": [
                "Session high/low sweep followed by immediate 1m rejection pinbar and displacement back inside the range."
            ],
            "failure_anti_patterns": [
                "Chasing momentum after candle has already closed far past the sweep wick extreme.",
                "Trading into flash spread blowout (> 40 points) on rapid intraday spikes."
            ],
            "last_updated": datetime.now(timezone.utc).isoformat()
        },
        "H1_PULLBACK_LIMIT": {
            "total_attempts": 6,
            "successful_trades": 5,
            "failed_trades": 1,
            "confidence_score": 0.833,
            "winning_signatures": [
                "H4 200 EMA trend alignment, primary trade running >= 1:1 with BE locked, limit placed at 50%-61.8% equilibrium."
            ],
            "failure_anti_patterns": [
                "Adding scale-in exposure when primary trade SL has not yet been locked at breakeven."
            ],
            "last_updated": datetime.now(timezone.utc).isoformat()
        },
        "SWING_M30_BOS_LIMIT": {
            "total_attempts": 12,
            "successful_trades": 9,
            "failed_trades": 3,
            "confidence_score": 0.75,
            "winning_signatures": [
                "Clear M30 Break of Structure in direction of D1/H4 flow, Alpha Vantage 10Y Yield check passed."
            ],
            "failure_anti_patterns": [
                "Entering swing buy when 10Y Treasury Yield is accelerating upward with Bullish DXY."
            ],
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    }

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def load_matrix(cls) -> Dict[str, Any]:
        return cls.get_instance()._load_matrix_from_disk()

    @classmethod
    def get_setup_cluster(cls, setup_type: str) -> Dict[str, Any]:
        inst = cls.get_instance()
        _, cluster = inst.get_setup_confidence(setup_type)
        return cluster

    @classmethod
    def get_historical_journals_for_setup(cls, setup_type: str, limit_per_status: int = 2) -> Dict[str, Any]:
        inst = cls.get_instance()
        res = inst.get_recent_journals_for_setup(setup_type, num_wins=limit_per_status, num_losses=limit_per_status)
        return {
            "last_wins": res.get("winning_trades", []),
            "last_losses": res.get("losing_trades", [])
        }

    @classmethod
    def record_outcome(cls, setup_type: str, is_win: bool, signature_or_anti_pattern: str, ticket: Optional[int] = None) -> Dict[str, Any]:
        return cls.get_instance().record_trade_outcome(setup_type, is_win, signature_or_anti_pattern, ticket)

    def __init__(self, matrix_path: str = EXPERIENCE_MATRIX_PATH):
        self.matrix_path = matrix_path
        os.makedirs(os.path.dirname(self.matrix_path), exist_ok=True)
        self._ensure_matrix_file()

    def _ensure_matrix_file(self):
        """Ensures experience_matrix.json exists with baseline clusters."""
        if not os.path.exists(self.matrix_path):
            try:
                with open(self.matrix_path, "w", encoding="utf-8") as f:
                    json.dump(self.DEFAULT_CLUSTERS, f, indent=2)
                logger.info(f"[Experience Engine] Initialized new experience matrix at {self.matrix_path}")
            except Exception as e:
                logger.error(f"Failed to create experience matrix: {e}")
        else:
            # Validate contents
            try:
                with open(self.matrix_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not data or not isinstance(data, dict):
                    with open(self.matrix_path, "w", encoding="utf-8") as f:
                        json.dump(self.DEFAULT_CLUSTERS, f, indent=2)
            except Exception:
                with open(self.matrix_path, "w", encoding="utf-8") as f:
                    json.dump(self.DEFAULT_CLUSTERS, f, indent=2)

    def _load_matrix_from_disk(self) -> Dict[str, Any]:
        """Loads matrix data from JSON."""
        try:
            with open(self.matrix_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading experience matrix: {e}")
            return self.DEFAULT_CLUSTERS.copy()

    def save_matrix(self, matrix: Dict[str, Any]):
        """Persists updated matrix data to JSON."""
        try:
            with open(self.matrix_path, "w", encoding="utf-8") as f:
                json.dump(matrix, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving experience matrix: {e}")

    def normalize_cluster_name(self, setup_type: Optional[str]) -> str:
        """Maps diverse technique descriptions to standardized cluster names."""
        if not setup_type:
            return "M5_FVG_LIMIT"
        s = setup_type.upper()
        if "SWEEP" in s or "LIQUIDITY" in s:
            return "M1_SWEEP_RETEST"
        elif "SCALE" in s or "PULLBACK" in s or "SCALEIN" in s:
            return "H1_PULLBACK_LIMIT"
        elif "SWING" in s or "H1" in s or "M30" in s:
            return "SWING_M30_BOS_LIMIT"
        elif "FVG" in s or "LIMIT" in s or "M5" in s:
            return "M5_FVG_LIMIT"
        return "M5_FVG_LIMIT"

    def record_trade_outcome(
        self,
        setup_type: str,
        is_win: bool,
        signature_or_anti_pattern: str,
        ticket: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Dual-sided learning record:
        Updates successful_trades, failed_trades, total_attempts, and confidence_score.
        Appends winning setup signature or failure anti-pattern.
        """
        cluster_name = self.normalize_cluster_name(setup_type)
        matrix = self._load_matrix_from_disk()

        if cluster_name not in matrix:
            matrix[cluster_name] = {
                "total_attempts": 0,
                "successful_trades": 0,
                "failed_trades": 0,
                "confidence_score": 0.50,
                "winning_signatures": [],
                "failure_anti_patterns": [],
                "last_updated": datetime.now(timezone.utc).isoformat()
            }

        cluster = matrix[cluster_name]
        cluster["total_attempts"] = cluster.get("total_attempts", 0) + 1

        if is_win:
            cluster["successful_trades"] = cluster.get("successful_trades", 0) + 1
            sigs = cluster.get("winning_signatures", [])
            if signature_or_anti_pattern and signature_or_anti_pattern not in sigs:
                sigs.insert(0, f"[#{ticket or 'N/A'}] {signature_or_anti_pattern}")
                cluster["winning_signatures"] = sigs[:5]  # Keep top 5
        else:
            cluster["failed_trades"] = cluster.get("failed_trades", 0) + 1
            antis = cluster.get("failure_anti_patterns", [])
            if signature_or_anti_pattern and signature_or_anti_pattern not in antis:
                antis.insert(0, f"[#{ticket or 'N/A'}] {signature_or_anti_pattern}")
                cluster["failure_anti_patterns"] = antis[:5]  # Keep top 5

        total = cluster["total_attempts"]
        wins = cluster["successful_trades"]
        cluster["confidence_score"] = round(wins / max(1, total), 3)
        cluster["last_updated"] = datetime.now(timezone.utc).isoformat()

        self.save_matrix(matrix)
        logger.info(
            f"[Experience Engine] Updated {cluster_name}: Total={total}, Wins={wins}, "
            f"Confidence={cluster['confidence_score']*100:.1f}%. Outcome={'WIN' if is_win else 'LOSS'}."
        )
        return cluster

    def get_setup_confidence(self, setup_type: str) -> Tuple[float, Dict[str, Any]]:
        """
        Returns the historical confidence score (0.0 to 1.0) and cluster details.
        """
        cluster_name = self.normalize_cluster_name(setup_type)
        matrix = self._load_matrix_from_disk()
        cluster = matrix.get(cluster_name, self.DEFAULT_CLUSTERS.get(cluster_name, {
            "total_attempts": 5, "successful_trades": 3, "confidence_score": 0.60
        }))
        score = float(cluster.get("confidence_score", 0.60))
        return score, cluster

    def get_recent_journals_for_setup(
        self,
        setup_type: str,
        num_wins: int = 2,
        num_losses: int = 2
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Queries TradeJournal for the last 2 winning and last 2 losing trades.
        """
        db = SessionLocal()
        cluster_name = self.normalize_cluster_name(setup_type)
        try:
            # Query recent wins
            wins = db.query(TradeJournal).filter(
                TradeJournal.status == "PASS"
            ).order_by(TradeJournal.closed_at.desc()).limit(num_wins).all()

            # Query recent losses
            losses = db.query(TradeJournal).filter(
                (TradeJournal.status == "FAIL") | (TradeJournal.realized_pnl < -5.0)
            ).order_by(TradeJournal.closed_at.desc()).limit(num_losses).all()

            def format_j(t: TradeJournal) -> Dict[str, Any]:
                return {
                    "ticket": t.ticket,
                    "side": t.side,
                    "lot_size": t.lot_size,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.realized_pnl,
                    "technique": t.technique_used,
                    "reason": t.reason,
                    "why_it_went_wrong": t.why_it_went_wrong,
                    "lessons_learned": t.lessons_learned
                }

            return {
                "cluster": cluster_name,
                "winning_trades": [format_j(w) for w in wins],
                "losing_trades": [format_j(l) for l in losses]
            }
        except Exception as e:
            logger.error(f"Error querying journals for setup {setup_type}: {e}")
            return {"cluster": cluster_name, "winning_trades": [], "losing_trades": []}
        finally:
            db.close()
