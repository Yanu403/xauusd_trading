<div align="center">

# ⚡ XAUUSD Trading Bot

**Production-grade algorithmic trading system with risk-first architecture**

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Architecture](https://img.shields.io/badge/Architecture-Modular-orange)
![Status](https://img.shields.io/badge/Status-Active%20Development-yellow)

[Architecture](docs/architecture.md) · [Deployment Guide](docs/windows_deploy_playbook.md) · [Portfolio Spec](docs/session_basket_portfolio_spec_v1.md) · [Research Reports](docs/reports/)

</div>

---

## What Is This?

A modular, research-driven trading system for XAUUSD (Gold) and forex pairs, built around a single principle: **prove the alpha before building the system**.

Unlike typical trading bots that jump straight to execution, this project follows a disciplined research-first workflow — clean data → validated signals → strict risk management → safe execution → feedback loop → optional ML/LLM augmentation.

### Key Highlights

- 🔬 **Walk-forward validated** — every strategy passes out-of-sample testing before promotion
- 🛡️ **Risk-first design** — drawdown guards, kill switches, position sizing, and daily loss caps enforced at the engine level
- 📊 **Multi-strategy basket portfolio** — 6 strategy branches across 3 pairs with priority-based conflict resolution
- 🔄 **MT5 integration** — paper trading, demo, and live execution via MetaTrader 5
- 📱 **Telegram alerting** — optional real-time notifications for trade events
- 🧪 **Audit-driven development** — 65-point audit completed, 15 critical fixes applied
- 🤖 **ML/LLM roadmap** — planned integration for regime detection, feature engineering, and trade analysis

---

## Architecture

```
Market Data
    │
    ▼
┌─────────────────────┐
│  Data Validation &   │  ← OHLCV cleaning, gap detection, spread sanity
│  Normalization       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Feature Pipeline    │  ← ATR, RSI, session features, candle structure
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Strategy Engine     │  ← Signal candidates with entry/SL/TP + reason tags
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Risk Engine         │  ← Position sizing, exposure caps, drawdown guard, kill switch
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Execution Adapter   │  ← Paper / MT5 dry-run / MT5 live, with idempotent order handling
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Reporting &         │  ← Trade ledger, metrics, Telegram alerts, feedback loop
│  Monitoring          │
└─────────────────────┘
```

Each layer is independent. The strategy engine never touches broker logic. The risk engine is a hard gate before any execution. The execution adapter supports paper → dry-run → live promotion without code changes.

---

## Portfolio: Session Basket v1

The active portfolio uses **session-based strategies** — setups triggered by London/NY session dynamics across multiple forex pairs.

| Branch | Strategy | Pair | Risk/Trade | Role |
|--------|----------|------|-----------|------|
| `eurusd_sweep` | Session Sweep + FVG | EURUSD | 1.00% | Primary alpha |
| `eurusd_continuation` | Session Continuation | EURUSD | 0.75% | Momentum capture |
| `eurusd_orb` | Opening Range Breakout | EURUSD | 0.50% | Flow engine |
| `gbpusd_sweep` | Session Sweep (adapted) | GBPUSD | 0.60% | Cross-pair expansion |
| `gbpusd_continuation` | Session Continuation | GBPUSD | 0.25% | Supplementary |
| `xauusd_continuation` | Session Continuation | XAUUSD | 0.75% | Gold momentum |

**Conflict resolution:** One position per symbol, branch priority ordering, cross-symbol overlap allowed.

**Latest basket performance:** ~4.5 setups/week across all branches, with 95%+ survival rate under single-position execution constraints.

---

## Project Structure

```
├── src/xauusd_trading/          # Core reusable package
│   ├── backtesting/             # Unified backtest engine (next-bar open, spread-aware)
│   ├── config/                  # Path management, config loading
│   ├── execution/               # MT5 adapter, paper trade, portfolio runner, alerts
│   ├── features/                # Indicators (ATR, RSI Wilder's), feature builder
│   ├── models/                  # Trade/position models (live, paper, trading)
│   ├── reporting/               # Metrics, trade ledger export
│   ├── risk/                    # Risk manager (drawdown, consecutive loss, position cap)
│   └── strategies/              # Strategy implementations + base interface
│
├── research/                    # Experiments, walk-forward validation, cross-asset
│   ├── experiments/             # Strategy-specific research (EURUSD sweep, M15, etc.)
│   └── walkforward/             # No-look-ahead validation scripts
│
├── docs/                        # Architecture, specs, deploy guides, audit reports
│   └── reports/                 # Per-round backtest & basket analysis reports
│
├── runtime/                     # Sample configs (secrets excluded via .gitignore)
├── tests/                       # Test suite
│
├── run_session_basket_demo_mt5.py      # MT5 paper/demo runner
├── run_session_basket_execution_mt5.py # MT5 execution runner (dry-run default)
├── run_basket_backtest.py              # Portfolio-level backtest
└── windows_*.bat                       # Windows RDP launchers
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- For MT5 integration: Windows with [MetaTrader 5](https://www.metatrader5.com/) installed

### Installation

```bash
git clone https://github.com/Yanu403/xauusd_trading.git
cd xauusd_trading
pip install -r requirements.txt
```

### Run a Backtest

```bash
python run_basket_backtest.py
```

### Run Paper Trading (CSV mode)

```bash
python run_paper_trade.py --send-telegram-alerts
```

### Run MT5 Paper/Demo (Windows)

```bash
python run_session_basket_demo_mt5_loop.py
```

### Run MT5 Execution (Windows, dry-run by default)

```bash
python run_session_basket_execution_mt5_loop.py
# Add --allow-live-send to enable real order submission
```

> 📖 See [Windows Deployment Playbook](docs/windows_deploy_playbook.md) for full setup instructions.

---

## Design Philosophy

### Why No ML/LLM Yet?

This project follows a strict dependency chain:

```
1. Clean data
2. Valid signal — no look-ahead bias
3. Strict risk management
4. Safe execution
5. Feedback & monitoring
6. ML only if it provably adds edge
7. LLM only as support layer (trade journal, regime annotation)
```

Building ML infrastructure before proving alpha is engineering theater. The current priority is validating the basket portfolio under live market conditions.

### Hard Rules

- ❌ No look-ahead bias (enforced via backward-only rolling + shift(1))
- ❌ No hidden future leakage
- ❌ No live trading before paper validation
- ❌ No overengineering before reusable core exists
- ✅ Every phase must pass audit before expanding scope

---

## Roadmap

### ✅ Phase A — Foundation (Done)
- Shared data loader, indicator pipeline, strategy interface
- Unified backtest engine with realistic execution modeling
- Risk manager with drawdown guards and kill switch

### ✅ Phase B — Core Engine (Done)
- Trade/position models
- Metrics and trade ledger
- Atomic state persistence (crash-safe)

### ✅ Phase C — Safety Layer (Done)
- MT5 execution adapter with dry-run default
- Portfolio-level conflict resolution
- Telegram alerting hook

### 🔄 Phase D — Production Hardening (In Progress)
- Broker reconciliation for multi-position stateful management
- Restart-safe state handling across process crashes
- Tighter risk caps for live deployment
- Cross-regime validation (2022-2026 stress testing)

### 🔮 Phase E — Intelligence Layer (Planned)
- **ML Feature Engineering** — regime detection, volatility clustering, session classification
- **ML Signal Enhancement** — model-augmented entry/exit timing (only if proven via walk-forward)
- **LLM Trade Analysis** — automated trade journaling, performance commentary, anomaly explanation
- **LLM Operator Assistant** — natural language query over trade history and risk metrics

### 🌐 Phase F — Expansion (Planned)
- Multi-pair scaling with pair-specific parameter optimization
- Web dashboard for portfolio monitoring
- REST API for external signal consumption
- Backtest-as-a-service for strategy research

---

## Research Methodology

Every strategy goes through a rigorous validation pipeline:

1. **Hypothesis** — define the market behavior being exploited
2. **Implementation** — code the strategy with strict no-look-ahead rules
3. **In-sample backtest** — initial validation on training data
4. **Walk-forward validation** — out-of-sample testing on unseen data
5. **Audit** — bias detection, execution realism, risk sizing review
6. **Basket integration** — portfolio-level conflict and overlap analysis
7. **Paper trading** — live market validation via MT5 demo
8. **Promotion** — only after all gates pass

Research reports for each round are preserved in [`docs/reports/`](docs/reports/) for full transparency.

---

## Contributing

Contributions are welcome. Please read the [architecture docs](docs/architecture.md) before submitting changes.

Areas where help is especially valuable:
- Strategy research (new session patterns, additional pairs)
- ML feature engineering for regime detection
- Risk management improvements
- Documentation and testing

---

## Disclaimer

⚠️ **This is a research project, not financial advice.** Trading forex and gold involves substantial risk of loss. The strategies in this repository have not been proven profitable in live trading. Use at your own risk. Past backtest performance does not guarantee future results.

---

## License

[MIT](LICENSE) — use it, fork it, learn from it.

---

<div align="center">

**Built with discipline, not hype.**

*"Don't build a sophisticated system before the alpha is proven."*

</div>
