import os
import sys
import uvicorn

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

PORT = int(os.environ.get("DASHBOARD_PORT", 8080))

if __name__ == "__main__":
    print("=" * 60)
    print("AUTONOMOUS TRADING AI - COMMAND CENTER & TRADE JOURNAL")
    print(f"Dashboard available at: http://localhost:{PORT}")
    print("=" * 60)
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=False, log_level="warning")
