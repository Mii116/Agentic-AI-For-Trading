# Autonomous Trading AI Platform

## Project Purpose
A production-oriented autonomous market research and algorithmic trading platform capable of generating trading hypotheses, backtesting, paper trading, and executing trades via HFM MT5 in a highly controlled manner.

## Architecture
- **Reasoning Layer**: Gemini AI for research, interpretation, and hypothesis generation.
- **Quantitative/Enforcement Layer**: Deterministic Python algorithms for indicators, position sizing, and risk enforcement.
- **Execution Layer**: Generic Broker abstraction with `PaperBroker` and `MT5Broker`.

## Operating Modes
1. DEMO
2. BACKTEST
3. PAPER
4. MT5_DEMO
5. MT5_LIVE (Requires extreme validation and human authorization)

## Setup & Run
See `docs/setup.md` for full installation instructions.

```bash
# Start the backend via Docker
docker-compose up --build
```
