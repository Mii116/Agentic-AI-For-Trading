import csv
import io
import json
import logging
from typing import List, Dict, Any, Optional

from app.db.session import SessionLocal
from app.models.trading import TradeJournal

logger = logging.getLogger(__name__)

class ReportExporter:
    """
    Trade Journal & Audit Exporter:
    - Generates CSV downloads of closed trades and journal entries.
    - Generates JSON dumps for quantitative backtesting analysis.
    - Produces clean Markdown executive summaries of algorithmic performance.
    """

    @classmethod
    def export_to_csv(cls, magic_number: Optional[int] = None) -> str:
        db = SessionLocal()
        try:
            query = db.query(TradeJournal)
            if magic_number:
                query = query.filter(TradeJournal.magic_number == magic_number)
            trades = query.order_by(TradeJournal.closed_at.desc()).all()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "ID", "Ticket", "Symbol", "Side", "Volume", "EntryPrice", "ExitPrice",
                "StopLoss", "TakeProfit", "PnL", "Status", "Magic", "PartialClosed",
                "IsScaleIn", "Technique", "Reason", "ExitReason", "WhyWentWrong", "LessonsLearned", "OpenedAt", "ClosedAt"
            ])

            for t in trades:
                writer.writerow([
                    t.id, t.ticket, t.symbol, t.side, t.lot_size, t.entry_price, t.exit_price,
                    t.stop_loss, t.take_profit, t.realized_pnl, t.status, t.magic_number,
                    t.partial_closed, t.is_scale_in, t.technique_used, t.reason, t.exit_reason,
                    t.why_it_went_wrong, t.lessons_learned,
                    t.opened_at.isoformat() if t.opened_at else "",
                    t.closed_at.isoformat() if t.closed_at else ""
                ])

            return output.getvalue()
        finally:
            db.close()

    @classmethod
    def export_to_markdown_summary(cls) -> str:
        from app.reports.performance import PerformanceReporter
        overall = PerformanceReporter.generate_performance_metrics()
        swing = PerformanceReporter.generate_performance_metrics(magic_number=1001)
        scalp = PerformanceReporter.generate_performance_metrics(magic_number=2002)

        md = f"""# Institutional Algorithmic Trading Performance Report

### Overall Account Summary
- **Total Trades Closed:** {overall.get('total_trades', 0)}
- **Win Rate:** {overall.get('win_rate_pct', 0.0)}%
- **Profit Factor:** {overall.get('profit_factor', 0.0)}
- **Net Realized PnL:** ${overall.get('net_pnl', 0.0):.2f}
- **Max Drawdown:** ${overall.get('max_drawdown_dollars', 0.0):.2f}
- **Expectancy:** ${overall.get('expectancy', 0.0):.2f} per trade

---

### Segregated Strategy Breakdown
| Metric | Swing Trader (Magic: 1001) | Scalp Trader (Magic: 2002) |
| :--- | :---: | :---: |
| **Trades** | {swing.get('total_trades', 0)} | {scalp.get('total_trades', 0)} |
| **Win Rate** | {swing.get('win_rate_pct', 0.0)}% | {scalp.get('win_rate_pct', 0.0)}% |
| **Profit Factor** | {swing.get('profit_factor', 0.0)} | {scalp.get('profit_factor', 0.0)} |
| **Net PnL** | ${swing.get('net_pnl', 0.0):.2f} | ${scalp.get('net_pnl', 0.0):.2f} |
| **Avg Win / Avg Loss** | ${swing.get('avg_win', 0.0):.2f} / ${swing.get('avg_loss', 0.0):.2f} | ${scalp.get('avg_win', 0.0):.2f} / ${scalp.get('avg_loss', 0.0):.2f} |
"""
        return md
