# MT5 Runtime Plan

The next deployment focus is **Windows RDP + MT5 terminal**.

## Why MT5 Runtime

For realistic paper trading / live trading, the bot needs:
- continuously updating bar data
- access to the correct broker symbol
- an order/execution path close to the broker terminal
- a setup that is easy to migrate to Windows RDP

Therefore, the current architecture is directed toward:
1. Python strategy engine remains separate
2. MT5 serves as the data bridge, and later the execution bridge
3. Telegram remains the alert layer

## Existing Initial Components

- `src/xauusd_trading/data/mt5.py`
  - init/login/shutdown MT5
  - fetch OHLCV bars from the MT5 terminal
  - normalize to a DataFrame compatible with the current strategy engine
- `src/xauusd_trading/execution/mt5_execution.py`
  - broker position snapshot
  - build order intent from signal
  - decision engine: open / reverse / sync SLTP / manage position / hold
  - dry-run by default; live send only when explicitly enabled
- `run_paper_trade_mt5.py`
  - one-shot paper-trade run directly from MT5 bars
- `run_paper_trade_mt5_loop.py`
  - periodic loop for polling MT5 bars and sending Telegram alerts on events
- `run_mt5_execution.py`
  - execution runner for bridging signals to MT5 requests

## Local Config Format

Add to `runtime/paper_trade_config.json` (this file is local-only, not tracked in git):

```json
{
  "telegram": {
    "bot_token": "...",
    "chat_id": "..."
  },
  "mt5": {
    "symbol": "XAUUSD",
    "timeframe": "H1",
    "bars": 1500,
    "terminal_path": "C:/Program Files/MetaTrader 5 terminal64.exe",
    "login": 12345678,
    "password": "...",
    "server": "Broker-Server"
  }
}
```

## Example Usage

### One-shot

```bash
python run_paper_trade_mt5.py --send-telegram-alerts --json
```

### Periodic Loop

```bash
python run_paper_trade_mt5_loop.py \
  --interval-seconds 300 \
  --send-telegram-alerts
```

## Important Notes

- This runner requires the Python `MetaTrader5` package in the Windows environment where the MT5 terminal is installed.
- `run_mt5_execution.py` can now build execution requests and read broker positions, but it defaults to **DRY_RUN**.
- To actually send orders to the broker, you must explicitly add the `--allow-live-send` flag.
- The current execution path focuses on:
  - checking existing positions in the terminal
  - building intent to open new positions
  - reversing by closing the opposing position first if necessary
  - syncing SL/TP on positions in the same direction when signal levels change materially
  - managing same-direction positions with partial close thresholds and basic trailing-style SL sync
- What is still incomplete for production live:
  - full reconciliation of broker state vs. internal state for multi-position / edge cases
  - more detailed lifecycle handling after fill
  - richer live state storage to prevent repeated partial triggers across restarts
