from __future__ import annotations

import pandas as pd

from xauusd_trading.data.mt5 import MT5Config
from xauusd_trading.execution.mt5_execution import MT5ExecutionAdapter, MT5ExecutionConfig
from xauusd_trading.models.trading import TradeSignal
from xauusd_trading.risk.manager import RiskConfig
from xauusd_trading.strategies.session_orb_retest import SessionORBRetestStrategy


def test_gbpusd_orb_strategy_uses_forex_lot_size_metadata() -> None:
    strategy = SessionORBRetestStrategy(pip_size=0.0001, lot_size=100_000.0)
    signal = strategy._build_signal(  # noqa: SLF001 - regression test for strategy metadata
        pd.DataFrame(
            {
                "low": [1.35825],
                "high": [1.35910],
            },
            index=[pd.Timestamp("2026-05-11T06:30:00Z")],
        ),
        index=0,
        breakout_index=0,
        side="LONG",
        entry_price=1.35902,
        level=1.35900,
    )

    assert signal is not None
    assert signal.metadata["lot_size"] == 100_000.0


def test_live_build_intent_defaults_forex_to_100k_lot_size() -> None:
    adapter = MT5ExecutionAdapter(
        mt5_config=MT5Config(symbol="GBPUSD"),
        execution_config=MT5ExecutionConfig(symbol="GBPUSD"),
    )
    signal = TradeSignal(
        index=1,
        timestamp=pd.Timestamp("2026-05-11T06:30:00Z"),
        side="LONG",
        entry_price=1.35902,
        stop_loss=1.35825,
        take_profit=1.36056,
        metadata={
            "symbol": "GBPUSD",
            "strategy": "gbpusd_orb",
            "risk_per_trade": 0.003,
            "pip_size": 0.0001,
            # Deliberately omit lot_size to verify safe forex fallback.
        },
    )

    intent = adapter.build_intent(
        signal,
        account_balance=10_000.0,
        risk_config=RiskConfig(risk_per_trade=0.01),
    )

    assert intent.metadata["lot_size"] == 100_000.0
    assert 0.37 <= intent.volume <= 0.40
    assert intent.metadata["estimated_risk_currency"] <= intent.metadata["risk_budget"] * 1.05


def test_buggy_ten_lot_gbpusd_trade_would_exceed_risk_budget() -> None:
    entry = 1.35902
    stop = 1.35825
    estimated_risk = 10.0 * 100_000.0 * abs(entry - stop)

    assert estimated_risk > 700.0
    assert estimated_risk > 30.0 * 20.0
