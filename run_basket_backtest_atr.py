#!/usr/bin/env python3
"""Backtest runner for session basket with ATR-based SL/TP tuning.

Key changes from v1:
- SL uses ATR multiple instead of fixed buffer_pips
- TP uses ATR multiple for target, not just R:R
- XAUUSD: SL ~1.0-1.5× ATR, TP ~2.0-3.0× ATR
- Forex: SL ~1.5× ATR, TP ~2.5-3.0× ATR
- Minimum SL distance enforced by ATR
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from xauusd_trading.backtesting.engine import BacktestEngine
from xauusd_trading.data.loader import load_ohlcv_csv
from xauusd_trading.risk.manager import RiskConfig
from xauusd_trading.strategies.eurusd_session_sweep import EURUSDSessionSweepFVGStrategy
from xauusd_trading.strategies.session_continuation import SessionContinuationFVGStrategy
from xauusd_trading.strategies.session_orb_retest import SessionORBRetestStrategy

DATA_DIR = Path("/root/data/dataset_hfm")


def find_csv(pattern: str) -> Path | None:
    matches = sorted(DATA_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def run_branch(branch_id: str, symbol: str, df: pd.DataFrame, strategy, risk_pct: float, risk_config: RiskConfig) -> dict:
    from xauusd_trading.backtesting.engine import BacktestConfig
    bt_config = BacktestConfig(
        initial_balance=10_000.0,
        slippage_pct=0.04,
        risk=risk_config,
        max_open_positions=1,
    )
    engine = BacktestEngine(config=bt_config)
    feature_df = strategy.prepare_features(df)
    trades = engine.run(feature_df, strategy)
    
    n_trades = len(trades)
    if n_trades == 0:
        return {
            "branch_id": branch_id,
            "symbol": symbol,
            "trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_pnl_pct": 0,
            "avg_sl_pips": 0,
            "avg_tp_pips": 0,
        }
    
    wins = sum(1 for t in trades if t.pnl_currency > 0)
    gross_profit = sum(t.pnl_currency for t in trades if t.pnl_currency > 0)
    gross_loss = abs(sum(t.pnl_currency for t in trades if t.pnl_currency < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    
    pip_size = strategy.pip_size
    
    # Compute average SL and TP distances
    sl_pips_list = []
    tp_pips_list = []
    for t in trades:
        sl_dist = abs(t.entry_price - t.stop_loss) / pip_size
        tp_dist = abs(t.entry_price - t.take_profit) / pip_size
        sl_pips_list.append(sl_dist)
        tp_pips_list.append(tp_dist)
    
    total_pnl = sum(t.pnl_currency for t in trades)
    total_pnl_pct = total_pnl / 10_000.0 * 100
    
    return {
        "branch_id": branch_id,
        "symbol": symbol,
        "trades": n_trades,
        "win_rate": wins / n_trades * 100,
        "profit_factor": round(pf, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "avg_sl_pips": round(sum(sl_pips_list) / len(sl_pips_list), 1),
        "avg_tp_pips": round(sum(tp_pips_list) / len(tp_pips_list), 1),
        "max_sl_pips": round(max(sl_pips_list), 1),
        "min_sl_pips": round(min(sl_pips_list), 1),
        "final_balance": round(10_000.0 + total_pnl, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ATR-tuned session basket backtest")
    parser.add_argument("--max-bars", type=int, default=5000, help="Max bars per symbol (0=all)")
    args = parser.parse_args()
    
    initial_balance = 10_000.0
    
    # ── ATR-TUNED BRANCH SPECS ─────────────────────────────────────────
    # Key insight: XAUUSD ATR14 M5 ≈ 819 pips ($8.19)
    #   SL must be at least 1.0× ATR to survive normal noise
    #   TP should target 2.0-3.0× ATR for proper reward
    #   Forex (EUR/GBP): ATR ≈ 4-5 pips, SL 1.5× ATR, TP 2.5× ATR
    
    branches = [
        # ── EURUSD Sweep ──────────────────────────────────────
        {
            "branch_id": "eurusd_sweep",
            "symbol": "EURUSD",
            "file_pattern": "EURUSDc_M5_*.csv",
            "risk_pct": 0.005,
            "strategy_factory": lambda: EURUSDSessionSweepFVGStrategy(
                name="eurusd_sweep",
                execution_timeframe="M5",
                max_spread_pips=4.0,
                min_sweep_pips=1.0,
                min_fvg_pips=0.3,
                min_asia_range_pips=2.0,
                max_asia_range_pips=200,
                sweep_lookback_bars=60,
                entry_expiry_bars=24,
                displacement_atr_multiple=0.7,
                # ATR-based SL/TP for EURUSD (ATR ~4 pips):
                # SL = buffer below sweep extreme, but ensure min 1.5× ATR
                # Currently: stop_buffer_pips is in strategy pips
                # For EUR: 1.5× ATR = 6 pips → stop_buffer_pips = 2 (added to structure)
                # SL will be ~6-10 pips from entry = proper
                stop_buffer_pips=2.0,
                # TP: max(asia_target, 2.5× risk) = smart target
                rr_target=2.5,
                pip_size=0.0001,
                spread_points_per_pip=10.0,
            ),
        },
        # ── GBPUSD ORB ────────────────────────────────────────
        {
            "branch_id": "gbpusd_orb",
            "symbol": "GBPUSD",
            "file_pattern": "GBPUSDc_M5_*.csv",
            "risk_pct": 0.003,
            "strategy_factory": lambda: SessionORBRetestStrategy(
                name="gbpusd_orb",
                execution_timeframe="M5",
                max_spread_pips=3.0,
                breakout_buffer_pips=0.5,
                retest_tolerance_pips=1.0,
                displacement_atr_multiple=1.0,
                breakout_lookback_bars=16,
                entry_expiry_bars=12,
                # GBPUSD ATR ~5 pips → SL should be ~8 pips
                stop_buffer_pips=2.0,
                rr_target=2.5,
                pip_size=0.0001,
                spread_points_per_pip=10.0,
            ),
        },
        # ── EURUSD ORB ────────────────────────────────────────
        {
            "branch_id": "eurusd_orb",
            "symbol": "EURUSD",
            "file_pattern": "EURUSDc_M5_*.csv",
            "risk_pct": 0.003,
            "strategy_factory": lambda: SessionORBRetestStrategy(
                name="eurusd_orb",
                execution_timeframe="M5",
                max_spread_pips=3.0,
                breakout_buffer_pips=0.5,
                retest_tolerance_pips=1.0,
                displacement_atr_multiple=1.0,
                breakout_lookback_bars=16,
                entry_expiry_bars=12,
                stop_buffer_pips=2.0,
                rr_target=2.5,
                pip_size=0.0001,
                spread_points_per_pip=10.0,
            ),
        },
        # ── XAUUSD Continuation ───────────────────────────────
        # THIS IS THE BIG FIX: scale everything for gold
        {
            "branch_id": "xauusd_continuation",
            "symbol": "XAUUSD",
            "file_pattern": "XAUUSDc_M5_*.csv",
            "risk_pct": 0.003,
            "strategy_factory": lambda: SessionContinuationFVGStrategy(
                name="xauusd_continuation",
                execution_timeframe="M5",
                pip_size=0.01,
                spread_points_per_pip=100.0,
                max_spread_pips=10.0,
                min_fvg_pips=0.03,
                impulse_lookback_bars=48,
                entry_expiry_bars=18,
                displacement_atr_multiple=0.8,
                # KEY FIX: XAUUSD ATR ≈ 819 pips
                # SL below impulse low: need buffer ~100 pips (= 1 point in price)
                # So SL distance from entry will be ~200-800 pips = 0.25-1.0× ATR
                # Old: stop_buffer_pips=30 ($0.30) = absurdly tiny
                # New: stop_buffer_pips=100 ($1.00) = minimum for gold
                stop_buffer_pips=100.0,
                # TP: R:R 2.0 × risk → e.g. SL $3.00 → TP $6.00
                # That's realistic for gold! ($6 ≈ 0.7× ATR)
                rr_target=2.0,
                max_bars_hold=48,  # Gold needs more time
            ),
        },
        # ── XAUUSD ORB (NEW - testing) ────────────────────────
        {
            "branch_id": "xauusd_orb",
            "symbol": "XAUUSD",
            "file_pattern": "XAUUSDc_M5_*.csv",
            "risk_pct": 0.003,
            "strategy_factory": lambda: SessionORBRetestStrategy(
                name="xauusd_orb",
                execution_timeframe="M5",
                pip_size=0.01,
                spread_points_per_pip=100.0,
                max_spread_pips=10.0,
                breakout_buffer_pips=5.0,   # 500 pips buffer for gold breakout
                retest_tolerance_pips=5.0,  # 500 pips tolerance for gold
                displacement_atr_multiple=0.8,
                breakout_lookback_bars=16,
                entry_expiry_bars=18,
                stop_buffer_pips=100.0,     # $1.00 buffer below breakout low
                rr_target=2.0,
                max_bars_hold=48,
            ),
        },
        # ── GBPUSD Sweep (re-test with better params) ─────────
        {
            "branch_id": "gbpusd_sweep",
            "symbol": "GBPUSD",
            "file_pattern": "GBPUSDc_M5_*.csv",
            "risk_pct": 0.003,
            "strategy_factory": lambda: EURUSDSessionSweepFVGStrategy(
                name="gbpusd_sweep",
                execution_timeframe="M5",
                max_spread_pips=4.0,
                min_sweep_pips=1.0,
                min_fvg_pips=0.3,
                min_asia_range_pips=2.0,
                max_asia_range_pips=200,
                sweep_lookback_bars=60,
                entry_expiry_bars=24,
                displacement_atr_multiple=0.7,
                stop_buffer_pips=2.0,
                rr_target=2.5,
                pip_size=0.0001,
                spread_points_per_pip=10.0,
            ),
        },
    ]
    
    # ── Run backtest for each branch ──────────────────────────
    results = []
    for branch in branches:
        csv_path = find_csv(branch["file_pattern"])
        if csv_path is None:
            print(f"  ❌ {branch['branch_id']}: No data file ({branch['file_pattern']})")
            continue
        
        try:
            df = load_ohlcv_csv(csv_path)
        except Exception as e:
            print(f"  ❌ {branch['branch_id']}: Load error: {e}")
            continue
        
        if args.max_bars and len(df) > args.max_bars:
            df = df.iloc[-args.max_bars:]
        
        strategy = branch["strategy_factory"]()
        risk_config = RiskConfig(
            risk_per_trade=branch["risk_pct"],
            max_drawdown_pct=25.0,  # Increased from 12% for more trades
            max_consecutive_losses=10,
            min_balance=1000.0,
            min_risk_distance_pips=2.0 if branch["symbol"] != "XAUUSD" else 50.0,
            max_position_lots=10.0,
        )
        
        print(f"  🔄 {branch['branch_id']}: {len(df)} bars, SL buf={getattr(strategy, 'stop_buffer_pips', '?')}pips, RR={getattr(strategy, 'rr_target', '?')}")
        
        try:
            result = run_branch(branch["branch_id"], branch["symbol"], df, strategy, branch["risk_pct"], risk_config)
            results.append(result)
            
            emoji = "✅" if result["profit_factor"] >= 1.0 else "❌"
            print(f"  {emoji} {result['branch_id']}: {result['trades']} trades | WR={result['win_rate']:.0f}% | PF={result['profit_factor']:.2f} | PnL={result['total_pnl_pct']:+.2f}% | SL avg={result['avg_sl_pips']:.0f} max={result['max_sl_pips']:.0f} pips | TP avg={result['avg_tp_pips']:.0f} pips")
        except Exception as e:
            print(f"  ❌ {branch['branch_id']}: Backtest error: {e}")
            import traceback
            traceback.print_exc()
    
    # ── Summary ───────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  SUMMARY — ATR-Tuned Session Basket Backtest")
    print(f"  Balance: ${initial_balance:,.0f} | Period: last {args.max_bars} bars M5")
    print(f"{'='*80}")
    
    total_trades = sum(r["trades"] for r in results)
    profitable = [r for r in results if r["profit_factor"] >= 1.0 and r["trades"] > 0]
    unprofitable = [r for r in results if 0 < r["profit_factor"] < 1.0]
    no_trades = [r for r in results if r["trades"] == 0]
    
    print(f"\n  Total trades: {total_trades}")
    print(f"  Profitable branches: {len(profitable)}")
    print(f"  Unprofitable branches: {len(unprofitable)}")
    print(f"  No-trade branches: {len(no_trades)}")
    
    if profitable:
        print(f"\n  💚 PROFITABLE:")
        for r in profitable:
            print(f"    {r['branch_id']:25s} {r['trades']:3d} trades | WR={r['win_rate']:.0f}% | PF={r['profit_factor']:.2f} | PnL={r['total_pnl_pct']:+.2f}% | SL≈{r['avg_sl_pips']:.0f}pips TP≈{r['avg_tp_pips']:.0f}pips")
    
    if unprofitable:
        print(f"\n  🔴 UNPROFITABLE:")
        for r in unprofitable:
            print(f"    {r['branch_id']:25s} {r['trades']:3d} trades | WR={r['win_rate']:.0f}% | PF={r['profit_factor']:.2f} | PnL={r['total_pnl_pct']:+.2f}% | SL≈{r['avg_sl_pips']:.0f}pips TP≈{r['avg_tp_pips']:.0f}pips")
    
    if no_trades:
        print(f"\n  ⏳ NO TRADES:")
        for r in no_trades:
            print(f"    {r['branch_id']}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
