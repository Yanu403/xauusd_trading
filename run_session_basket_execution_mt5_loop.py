#!/usr/bin/env python3
"""Periodic MT5 execution scheduler for session basket portfolio.

Outputs a beautiful terminal dashboard instead of raw JSON.
Use --json for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    _shutdown = True
    logger.info("Received signal %s – setting shutdown flag", signum)


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from run_session_basket_execution_mt5 import build_parser as build_single_parser
from run_session_basket_execution_mt5 import run_once
from xauusd_trading.execution.terminal_ui import (
    BANNER,
    format_heartbeat,
    format_signal_event,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parent = build_single_parser(add_help=False)
    parser = argparse.ArgumentParser(
        description='Periodic MT5 execution scheduler for session basket portfolio',
        parents=[parent],
        add_help=True,
    )
    parser.add_argument('--interval-seconds', type=int, default=180)
    parser.add_argument('--max-iterations', type=int, default=0, help='0 means run forever')
    parser.add_argument('--heartbeat-log', default=str(PROJECT_ROOT / 'runtime' / 'session_basket_execution_loop.jsonl'))
    return parser


def append_heartbeat(path: str | Path, payload: dict) -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload) + '\n')


def main() -> int:
    args = build_parser().parse_args()
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    iteration = 0
    session_signals = 0
    session_orders_filled = 0
    session_orders_rejected = 0
    session_telegram_sent = 0

    # Startup banner
    if not args.json:
        live_mode = "🔴 LIVE" if args.allow_live_send else "🔵 DRY-RUN"
        print(BANNER)
        print(f"  Mode: {live_mode}  │  Loop: every {args.interval_seconds}s  │  Press Ctrl+C to stop")
        print()

    while not _shutdown:
        iteration += 1
        try:
            payload = run_once(args)
        except Exception:
            logger.exception("run_once failed on iteration %d – continuing loop", iteration)
            time.sleep(max(args.interval_seconds, 1))
            continue

        per_symbol = payload.get('per_symbol', {})
        decision_map = {symbol: item.get('decision', {}).get('action') for symbol, item in per_symbol.items()}
        live_actions = sum(1 for action in decision_map.values() if action and action != 'HOLD')
        accepted_count = len(payload.get('accepted_signals', []))
        rejected_count = len(payload.get('rejected_signals', []))
        telegram_sent = int(payload.get('telegram_alerts_sent', 0))
        branch_debugs = payload.get('branch_debugs', [])

        # Current-iteration broker order outcomes. These are different from
        # strategy signal conflicts: a signal can exist, but broker can reject,
        # or the order can fill and hit SL before next heartbeat.
        orders_filled_now = 0
        orders_rejected_now = 0
        for item in per_symbol.values():
            decision = item.get('decision', {})
            send_result = decision.get('send_result') or {}
            if decision.get('action') in {'OPEN', 'REVERSE'} and send_result:
                if send_result.get('sent'):
                    orders_filled_now += 1
                else:
                    orders_rejected_now += 1

        session_signals += accepted_count
        session_orders_filled += orders_filled_now
        session_orders_rejected += orders_rejected_now
        session_telegram_sent += telegram_sent

        # Build symbol info for heartbeat
        symbols_info = {}
        for symbol, item in per_symbol.items():
            decision = item.get('decision', {})
            symbols_info[symbol] = {
                'action': decision.get('action'),
                'reason': decision.get('reason'),
                'latest_bar_time': item.get('latest_bar_time'),
                'positions': len(item.get('broker_positions', [])),
            }

        heartbeat = {
            'timestamp': utc_now(),
            'iteration': iteration,
            'mode': payload.get('mode'),
            'accepted_signals': accepted_count,
            'rejected_signals': rejected_count,
            'live_actions': live_actions,
            'orders_filled_now': orders_filled_now,
            'orders_rejected_now': orders_rejected_now,
            'session_signals': session_signals,
            'session_orders_filled': session_orders_filled,
            'session_orders_rejected': session_orders_rejected,
            'telegram_alerts_sent': telegram_sent,
            'session_telegram_sent': session_telegram_sent,
            'debug_summary': payload.get('debug_summary', 'none'),
            'symbols': symbols_info,
        }

        append_heartbeat(args.heartbeat_log, heartbeat)

        if args.json:
            print(json.dumps({'heartbeat': heartbeat, 'payload': payload}, indent=2))
        else:
            # Beautiful dashboard output
            open_pos = sum(v.get('positions', 0) for v in symbols_info.values())
            print(format_heartbeat(
                iteration=iteration,
                timestamp=heartbeat['timestamp'],
                mode=payload.get('mode', 'DRY_RUN'),
                accepted=accepted_count,
                rejected=rejected_count,
                open_positions=open_pos,
                live_actions=live_actions,
                telegram_sent=telegram_sent,
                orders_filled=orders_filled_now,
                orders_rejected=orders_rejected_now,
                session_signals=session_signals,
                session_orders_filled=session_orders_filled,
                session_orders_rejected=session_orders_rejected,
                session_telegram_sent=session_telegram_sent,
                debug_summary=heartbeat['debug_summary'],
                symbols=symbols_info,
                branch_debugs=branch_debugs,
            ))

            # Show decision events with nice formatting
            for symbol, item in per_symbol.items():
                decision = item.get('decision', {})
                if decision.get('action') != 'HOLD':
                    print(format_signal_event(decision))

            print()  # blank line between iterations

        if args.max_iterations and iteration >= args.max_iterations:
            break
        time.sleep(max(args.interval_seconds, 1))

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
