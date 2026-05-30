# Windows RDP Deploy Checklist

The purpose of this document is to make MT5 runtime deployment as **plug and play** as possible.

## 1. Basic Installation on Windows RDP

- Install **Python 3.11+**
- Install **MetaTrader 5 terminal**
- Log in to your broker account in the MT5 terminal
- Ensure the symbol you intend to use is available, e.g. `XAUUSD` or `XAUUSDm`

## 2. Prepare the Project

Copy the `projects/xauusd_trading/` folder to Windows RDP.

For the most convenient path, also use these files directly:
- `windows_setup_venv.bat`
- `windows_run_dry_loop.bat`
- `windows_run_live_loop.bat`
- `runtime/paper_trade_config.windows.sample.json`

The minimum required structure:

```text
xauusd_trading/
├── run_paper_trade_mt5.py
├── run_paper_trade_mt5_loop.py
├── run_mt5_execution.py
├── run_mt5_execution_loop.py
├── runtime/
│   ├── paper_trade_config.json
│   └── ...state files...
└── src/
```

## 3. Install Python Dependencies

It is recommended to install these in a virtual environment:

```bash
pip install pandas numpy MetaTrader5
```

## 4. Fill in Local Runtime Config

Edit the file:

`runtime/paper_trade_config.json`

The fastest approach:
1. Copy `runtime/paper_trade_config.windows.sample.json`
2. Rename to `runtime/paper_trade_config.json`
3. Fill in real credentials

Ensure the following sections are correct:

- `telegram.bot_token`
- `telegram.chat_id`
- `telegram.insecure_ssl` if the Windows environment has issues with Telegram SSL
- `telegram.alert_all_decisions` if you want all decisions, including `HOLD`, to be sent
- `mt5.symbol`
- `mt5.timeframe`
- `mt5.terminal_path`
- `mt5.login`
- `mt5.password`
- `mt5.server`
- `execution.lot_step`
- `execution.min_lot`
- `execution.max_lot`

## 5. Test Incrementally — Do Not Jump to Live

### Step A: Check Help

```bash
python run_mt5_execution.py --help
```

### Step B: Dry-run Execution

```bash
python run_mt5_execution.py --json
```

What to verify:
- MT5 can connect
- Data can be fetched
- Signals can be calculated
- Decisions are generated normally
- No tracebacks

### Step C: Periodic Dry-run

Use the MT5 paper trade loop to observe the repeating workflow.

### Step D: Execution Loop Dry-run

```bash
python run_mt5_execution_loop.py --interval-seconds 300 --json
```

### Step E: Live — Very Carefully

```bash
python run_mt5_execution.py --allow-live-send --send-telegram-alerts --json
```

Or for continuous mode:

```bash
python run_mt5_execution_loop.py --interval-seconds 300 --allow-live-send --send-telegram-alerts
```

## 6. Important Runtime Files

- `runtime/paper_trade_config.json`
- `runtime/mt5_execution_state.json`
- `runtime/*.jsonl`

### Why `mt5_execution_state.json` Is Important

This file stores live management state such as:
- whether partial close has already been performed
- last SL/TP sync

Without this file, the bot is more likely to repeat management actions after a restart.

## 7. Recommended Startup Sequence

The safest order:
1. `run_paper_trade_mt5.py`
2. `run_paper_trade_mt5_loop.py`
3. `run_mt5_execution.py` without `--allow-live-send`
4. `run_mt5_execution_loop.py` without `--allow-live-send`
5. `run_mt5_execution.py` or `run_mt5_execution_loop.py` with `--allow-live-send`

## 8. Before Going Live for the First Time

Final checklist:
- [ ] Broker symbol is correct
- [ ] Lot step matches the broker
- [ ] The account being used is the correct one
- [ ] Telegram alerts are arriving normally
- [ ] Dry-run decisions look reasonable
- [ ] MT5 terminal is stable and not requesting re-login
- [ ] VPS/RDP timezone is properly understood
- [ ] Runtime state file is not subject to unexpected sync or overwriting

## 9. Honest Notes

This runtime is robust enough for serious validation, but it is not yet a final, polished product.
Areas that require close monitoring during early live trading:
- multi-position edge cases
- repeated management actions after rapid broker condition changes
- more complex trailing/partial lifecycle handling
