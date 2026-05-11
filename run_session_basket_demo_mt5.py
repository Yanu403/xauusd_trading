#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / 'runtime' / 'session_basket_demo_config.json'
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from xauusd_trading.data.mt5 import MT5Config, fetch_ohlcv_from_mt5
from xauusd_trading.execution.alerts import TelegramAlertConfig, dispatch_telegram_alerts
from xauusd_trading.execution.paper import PaperTrader, load_paper_state, save_paper_state
from xauusd_trading.execution.portfolio import compact_debug_summary
from xauusd_trading.models.trading import TradeSignal
from xauusd_trading.risk.manager import RiskConfig
from xauusd_trading.strategies.eurusd_session_sweep import EURUSDSessionSweepFVGStrategy
from xauusd_trading.strategies.session_continuation import SessionContinuationFVGStrategy
from xauusd_trading.strategies.session_orb_retest import SessionORBRetestStrategy


@dataclass(slots=True)
class BranchSpec:
    branch_id: str
    symbol: str
    priority: int
    risk_per_trade: float
    strategy: object


def load_runtime_config(path: str | Path) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text())


def build_parser(*, add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='MT5 demo basket runner for session portfolio v1', add_help=add_help)
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument('--state-dir', default=str(PROJECT_ROOT / 'runtime' / 'session_basket_states'))
    parser.add_argument('--journal-dir', default=str(PROJECT_ROOT / 'runtime' / 'session_basket_journals'))
    parser.add_argument('--export-dir', default='')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--send-telegram-alerts', action='store_true')
    parser.add_argument('--telegram-bot-token')
    parser.add_argument('--telegram-chat-id')
    return parser


def resolve_telegram(args: argparse.Namespace, runtime_config: dict) -> None:
    telegram_config = runtime_config.get('telegram', {})
    if not args.telegram_bot_token:
        args.telegram_bot_token = telegram_config.get('bot_token') or os.getenv('XAUUSD_TELEGRAM_BOT_TOKEN')
    if not args.telegram_chat_id:
        args.telegram_chat_id = telegram_config.get('chat_id') or os.getenv('XAUUSD_TELEGRAM_CHAT_ID')
    args.telegram_insecure_ssl = bool(
        telegram_config.get('insecure_ssl', False)
        or os.getenv('XAUUSD_TELEGRAM_INSECURE_SSL') in {'1', 'true', 'TRUE', 'yes', 'YES'}
    )


def build_mt5_configs(runtime_config: dict) -> dict[str, MT5Config]:
    mt5_cfg = runtime_config.get('mt5', {})
    symbols_cfg = runtime_config.get('symbols', {})
    default_terminal = mt5_cfg.get('terminal_path') or os.getenv('XAUUSD_MT5_TERMINAL_PATH')
    default_login = mt5_cfg.get('login') or (int(os.getenv('XAUUSD_MT5_LOGIN')) if os.getenv('XAUUSD_MT5_LOGIN') else None)
    default_password = mt5_cfg.get('password') or os.getenv('XAUUSD_MT5_PASSWORD')
    default_server = mt5_cfg.get('server') or os.getenv('XAUUSD_MT5_SERVER')
    default_bars = int(mt5_cfg.get('bars', 2500))
    default_timeframe = mt5_cfg.get('timeframe', 'M3')

    configs: dict[str, MT5Config] = {}
    for symbol in ('EURUSD', 'GBPUSD', 'XAUUSD'):
        cfg = symbols_cfg.get(symbol, {})
        configs[symbol] = MT5Config(
            symbol=cfg.get('symbol', symbol),
            timeframe=cfg.get('timeframe', default_timeframe),
            bars=int(cfg.get('bars', default_bars)),
            terminal_path=cfg.get('terminal_path', default_terminal),
            login=cfg.get('login', default_login),
            password=cfg.get('password', default_password),
            server=cfg.get('server', default_server),
        )
    return configs


def build_branch_specs(runtime_config: dict, mt5_configs: dict[str, MT5Config]) -> list[BranchSpec]:
    spread_limits = runtime_config.get('spread_limits', {})
    spread_limits_pips = runtime_config.get('spread_limits_pips', {})
    spread_limits_points = runtime_config.get('spread_limits_points', {})

    def _resolve_spread(symbol_key: str, *, default_pips: float, spread_points_per_pip: float) -> float:
        mt5_symbol = (mt5_configs.get(symbol_key).symbol if mt5_configs.get(symbol_key) else symbol_key)
        for container, convert_points in (
            (spread_limits_pips, False),
            (spread_limits, False),
            (spread_limits_points, True),
        ):
            if not isinstance(container, dict):
                continue
            for k in (symbol_key, mt5_symbol):
                if k in container:
                    try:
                        raw = float(container[k])
                    except (TypeError, ValueError):
                        continue
                    return raw / spread_points_per_pip if convert_points else raw
        return default_pips

    eurusd_spread = _resolve_spread('EURUSD', default_pips=1.5, spread_points_per_pip=10.0)
    gbpusd_spread = _resolve_spread('GBPUSD', default_pips=1.5, spread_points_per_pip=10.0)
    xauusd_spread = _resolve_spread('XAUUSD', default_pips=4.0, spread_points_per_pip=100.0)

    # ── TUNED BRANCH SPECS (2026-05-06 ATR v2) ──────────────────────────
    # Key insight: SL/TP must scale with ATR, not fixed pips.
    # XAUUSD ATR14 ≈ 819 pips ($8.19) → stop_buffer must be 100+ pips
    # Forex ATR14 ≈ 4-5 pips → stop_buffer 2 pips is correct
    # See: run_basket_backtest_atr.py results (5K bars)
    # ──────────────────────────────────────────────────────────────────────
    active_branches = runtime_config.get('active_branches', [
        'xauusd_continuation',  # PF=1.27, +2.98%, 59 trades (BEST)
        'gbpusd_orb',           # PF=0.84 monitor
        'eurusd_orb',           # PF=0.75 monitor
        # DISABLED: eurusd_sweep (PF=0.16), gbpusd_sweep (PF=0.07)
        # DISABLED: xauusd_orb (PF=0.80, SL too wide)
    ])

    branches = []
    if 'gbpusd_orb' in active_branches:
        branches.append(BranchSpec(
            branch_id='gbpusd_orb',
            symbol='GBPUSD',
            priority=2,
            risk_per_trade=0.003,
            strategy=SessionORBRetestStrategy(
                execution_timeframe='M5',
                max_spread_pips=3.0,
                breakout_buffer_pips=0.5,
                retest_tolerance_pips=1.0,
                displacement_atr_multiple=1.0,
                breakout_lookback_bars=16,
                entry_expiry_bars=12,
                stop_buffer_pips=1.0,
                pip_size=0.0001,
                spread_points_per_pip=10.0,
                lot_size=100_000.0,
            ),
        ))
    if 'eurusd_orb' in active_branches:
        branches.append(BranchSpec(
            branch_id='eurusd_orb',
            symbol='EURUSD',
            priority=3,
            risk_per_trade=0.003,  # TUNED: 0.3%
            strategy=SessionORBRetestStrategy(
                execution_timeframe='M5',
                max_spread_pips=3.0,
                breakout_buffer_pips=0.5,
                retest_tolerance_pips=1.0,
                displacement_atr_multiple=1.0,
                breakout_lookback_bars=16,
                entry_expiry_bars=12,
                stop_buffer_pips=1.0,
                pip_size=0.0001,
                spread_points_per_pip=10.0,
                lot_size=100_000.0,
            ),
        ))
    if 'xauusd_continuation' in active_branches:
        branches.append(BranchSpec(
            branch_id='xauusd_continuation',
            symbol='XAUUSD',
            priority=1,  # BEST performer
            risk_per_trade=0.003,  # TUNED: was 0.0075 → 0.3%
            strategy=SessionContinuationFVGStrategy(
                execution_timeframe='M5',
                pip_size=0.01,
                spread_points_per_pip=100.0,
                max_spread_pips=10.0,          # TUNED: was 1.5
                min_fvg_pips=0.03,             # TUNED: was 1.0 (XAU scale)
                impulse_lookback_bars=48,       # TUNED: was 8
                entry_expiry_bars=18,           # TUNED: was 8
                displacement_atr_multiple=0.8,  # TUNED: was 1.4
                # KEY FIX: ATR-based stop buffer for gold
                # Old: stop_buffer_pips=30 ($0.30) → SL 30 pips → 10016 rejection
                # New: stop_buffer_pips=100 ($1.00) → SL avg 133 pips ($1.33)
                # This gives SL/ATR ratio ≈ 0.16× (still conservative)
                stop_buffer_pips=100.0,
                rr_target=2.0,
                max_bars_hold=48,  # Gold needs more time to reach TP
            ),
        ))
    # DISABLED branches (backtest results 2026-05-06 ATR v2):
    # - eurusd_sweep: PF=0.16, WR=18% (was profitable at PF=1.01 with old tight params but unrealistic)
    # - gbpusd_sweep: PF=0.07, WR=11%
    # - xauusd_orb: PF=0.80, SL avg 467 pips too wide

    return branches


def collect_candidates(df_by_symbol: dict[str, object], branch_specs: list[BranchSpec]) -> tuple[list[TradeSignal], list[dict]]:
    candidates: list[TradeSignal] = []
    branch_debugs: list[dict] = []
    for spec in branch_specs:
        df = df_by_symbol.get(spec.symbol)
        if df is None or len(df) < 30:
            branch_debugs.append(
                {
                    'branch_id': spec.branch_id,
                    'symbol': spec.symbol,
                    'priority': spec.priority,
                    'has_signal': False,
                    'reason_code': 'INSUFFICIENT_DATA',
                    'summary': 'Not enough bars loaded for this branch',
                    'details': {'rows': 0 if df is None else len(df)},
                }
            )
            continue
        feature_df = spec.strategy.prepare_features(df)
        signal = spec.strategy.generate_signal(feature_df, len(feature_df) - 1)
        debug_info = dict(spec.strategy.debug_signal(feature_df, len(feature_df) - 1))
        debug_info['branch_id'] = spec.branch_id
        debug_info['symbol'] = spec.symbol
        debug_info['priority'] = spec.priority
        branch_debugs.append(debug_info)
        if signal is None:
            continue
        signal.metadata['branch_id'] = spec.branch_id
        signal.metadata['branch_priority'] = spec.priority
        signal.metadata['symbol'] = spec.symbol
        signal.metadata['risk_per_trade'] = spec.risk_per_trade
        candidates.append(signal)
    return candidates, branch_debugs


def resolve_conflicts(candidates: list[TradeSignal]) -> tuple[list[TradeSignal], list[dict]]:
    accepted: list[TradeSignal] = []
    rejected: list[dict] = []
    by_symbol: dict[str, list[TradeSignal]] = {}
    for signal in candidates:
        symbol = str(signal.metadata.get('symbol', 'UNKNOWN'))
        by_symbol.setdefault(symbol, []).append(signal)

    for symbol, signals in by_symbol.items():
        ranked = sorted(signals, key=lambda s: (int(s.metadata.get('branch_priority', 999)), str(s.metadata.get('branch_id', ''))))
        winner = ranked[0]
        accepted.append(winner)
        for loser in ranked[1:]:
            rejected.append(
                {
                    'symbol': symbol,
                    'rejected_branch': loser.metadata.get('branch_id'),
                    'winner_branch': winner.metadata.get('branch_id'),
                    'reason': 'LOWER_PRIORITY_SAME_SYMBOL',
                    'entry_time': loser.timestamp.isoformat(),
                    'side': loser.side,
                }
            )

    accepted = sorted(accepted, key=lambda s: (str(s.metadata.get('symbol', '')), int(s.metadata.get('branch_priority', 999))))
    return accepted, rejected


def run_once(args: argparse.Namespace) -> dict:
    runtime_config = load_runtime_config(args.config)
    resolve_telegram(args, runtime_config)

    risk_cfg = runtime_config.get('risk', {})
    risk_config = RiskConfig(
        risk_per_trade=float(risk_cfg.get('risk_per_trade', 0.01)),
        max_drawdown_pct=float(risk_cfg.get('max_drawdown_pct', 12.0)),
        max_consecutive_losses=int(risk_cfg.get('max_consecutive_losses', 8)),
        min_balance=float(risk_cfg.get('min_balance', 1000.0)),
    )

    mt5_configs = build_mt5_configs(runtime_config)
    df_by_symbol = {}
    for symbol, cfg in mt5_configs.items():
        df = fetch_ohlcv_from_mt5(cfg)
        df_by_symbol[symbol] = df
        if args.export_dir:
            export_path = Path(args.export_dir) / f'{symbol.lower()}_{cfg.timeframe}.csv'
            export_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(export_path)

    branch_specs = build_branch_specs(runtime_config, mt5_configs)
    candidates, branch_debugs = collect_candidates(df_by_symbol, branch_specs)
    accepted_signals, rejected_signals = resolve_conflicts(candidates)
    accepted_by_symbol = {str(signal.metadata.get('symbol')): signal for signal in accepted_signals}
    debug_summary = compact_debug_summary(branch_debugs, limit=4)

    state_dir = Path(args.state_dir)
    journal_dir = Path(args.journal_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    journal_dir.mkdir(parents=True, exist_ok=True)

    per_symbol_payload: dict[str, dict] = {}
    all_events: list[dict] = []

    for symbol, cfg in mt5_configs.items():
        state_path = state_dir / f'{symbol.lower()}.json'
        journal_path = journal_dir / f'{symbol.lower()}.jsonl'
        state = load_paper_state(state_path, data_path=f'mt5://{cfg.symbol}/{cfg.timeframe}')
        trader = PaperTrader(state=state, risk_config=risk_config)
        symbol_signal = accepted_by_symbol.get(symbol)
        symbol_signals = [symbol_signal] if symbol_signal is not None else []
        updated_state = trader.step(df=df_by_symbol[symbol], signals=symbol_signals, journal_path=journal_path)
        save_paper_state(updated_state, state_path)

        symbol_events = []
        for event in trader.last_step_events:
            wrapped = dict(event)
            wrapped['symbol'] = symbol
            symbol_events.append(wrapped)
            all_events.append(wrapped)

        per_symbol_payload[symbol] = {
            'state_path': str(state_path),
            'journal_path': str(journal_path),
            'balance': updated_state.balance,
            'peak_balance': updated_state.peak_balance,
            'closed_trades': updated_state.closed_trades,
            'open_positions': [position.to_dict() for position in updated_state.open_positions],
            'latest_bar_time': df_by_symbol[symbol].index[-1].isoformat(),
            'event_count': len(symbol_events),
            'events': symbol_events,
        }

    telegram_sent = 0
    if args.send_telegram_alerts and args.telegram_bot_token and args.telegram_chat_id and all_events:
        telegram_sent = dispatch_telegram_alerts(
            TelegramAlertConfig(
                bot_token=args.telegram_bot_token,
                chat_id=args.telegram_chat_id,
                insecure_ssl=args.telegram_insecure_ssl,
            ),
            all_events,
        )

    return {
        'config_path': args.config,
        'state_dir': str(state_dir),
        'journal_dir': str(journal_dir),
        'risk': {
            'risk_per_trade_default': risk_config.risk_per_trade,
            'max_drawdown_pct': risk_config.max_drawdown_pct,
            'max_consecutive_losses': risk_config.max_consecutive_losses,
            'min_balance': risk_config.min_balance,
        },
        'candidates': [
            {
                'branch_id': signal.metadata.get('branch_id'),
                'symbol': signal.metadata.get('symbol'),
                'priority': signal.metadata.get('branch_priority'),
                'risk_per_trade': signal.metadata.get('risk_per_trade'),
                'side': signal.side,
                'entry_time': signal.timestamp.isoformat(),
                'entry_price': signal.entry_price,
                'stop_loss': signal.stop_loss,
                'take_profit': signal.take_profit,
            }
            for signal in candidates
        ],
        'accepted_signals': [
            {
                'branch_id': signal.metadata.get('branch_id'),
                'symbol': signal.metadata.get('symbol'),
                'priority': signal.metadata.get('branch_priority'),
                'risk_per_trade': signal.metadata.get('risk_per_trade'),
                'side': signal.side,
                'entry_time': signal.timestamp.isoformat(),
                'entry_price': signal.entry_price,
                'stop_loss': signal.stop_loss,
                'take_profit': signal.take_profit,
            }
            for signal in accepted_signals
        ],
        'branch_debugs': branch_debugs,
        'debug_summary': debug_summary,
        'rejected_signals': rejected_signals,
        'per_symbol': per_symbol_payload,
        'telegram_alerts_sent': telegram_sent,
        'spread_limits_effective': {
            spec.branch_id: getattr(spec.strategy, 'max_spread_pips', None)
            for spec in branch_specs
        },
    }


def main() -> int:
    args = build_parser().parse_args()
    payload = run_once(args)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        from xauusd_trading.execution.terminal_ui import format_execution_summary
        print(format_execution_summary(payload))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
