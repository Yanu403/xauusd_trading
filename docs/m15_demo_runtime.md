# M15 Demo Runtime

This track was created **separately from H1**.
Its purpose is not to replace the H1 branch, but to test the candidate **`SRSDM15LongRecentV2Strategy`** as a demo experiment based on the current regime.

## Key Files

- `run_paper_trade_mt5_m15.py`
- `run_paper_trade_mt5_m15_loop.py`
- `runtime/paper_trade_config.m15.windows.sample.json`
- `windows_run_m15_demo_loop.bat`

## Branch Characteristics

- Default timeframe: `M15`
- Strategy: `SRSDM15LongRecentV2Strategy`
- Mode: **paper/demo only**
- State, journal, and heartbeat are separated from H1

## Default Runtime Files

- State: `runtime/paper_trade_state.m15.json`
- Journal: `runtime/paper_trade_journal.m15.jsonl`
- Heartbeat: `runtime/paper_trade_mt5_m15_loop.jsonl`
- Config: `runtime/paper_trade_config.m15.json`

## Windows Setup

1. Copy `runtime/paper_trade_config.m15.windows.sample.json`
   to `runtime/paper_trade_config.m15.json`
2. Fill in MT5 and Telegram credentials
3. Ensure the broker symbol is correct and M15 history is available in MT5
4. Run:

```bat
windows_run_m15_demo_loop.bat
```

## Important Notes

- This does **not** use the H1 state/journal files
- This does **not** send live orders
- It is intentionally separated so that M15 observations do not interfere with the existing H1 runtime
- This candidate is **regime-specific**, not an all-weather system for 2022–2026

## Operational Recommendations

- Run in parallel with H1 only if Windows RDP resources are sufficient
- Monitor heartbeat logs to check whether M15 events are too frequent or noisy
- Do not promote to live until demo behavior has been sufficiently clean for several days
