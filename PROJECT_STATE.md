# PROJECT_STATE.md

## Project
XAUUSD Trading Bot

## Goal
Build a XAUUSD trading bot that:
- is research-validated
- free of look-ahead bias
- has strict risk management
- can progress gradually from research to paper trade and then to live

## Current state

### What exists now
- Reusable core package in `src/xauusd_trading/`
- Unified data loader + OHLCV validation
- Shared indicator/feature pipeline (ATR & RSI Wilder's smoothing)
- Single minimal strategy interface
- Unified backtest engine (next-bar open + spread, SL-first, prev-bar trailing)
- Risk manager (drawdown guard, consecutive-loss guard, max position cap, halt reset)
- Summary metrics module
- Trade ledger export
- Model validation (Side type, price invariants, entry_price > 0, etc.)
- Atomic state persistence (crash-safe)
- MT5 order rejection detection (retcode check)
- Risk enforcement in MT5 execution path
- Crash-proof loop scripts
- Telegram-ready alerting hook
- **FOCUS: Session basket strategies active** — see Active Portfolio below
- **Legacy single-pair strategies archived** — see `_archive_legacy/` and `_archive_legacy_runners/`

### What is done now (UPDATED 2026-05-05)
- **Full audit completed**: 65 findings across 13 categories, 15 P0 fixes applied
- **Look-ahead bias eliminated**: S/R detection uses backward-only rolling + shift(1)
- **Backtest engine hardened**: next-bar-open entry, spread-as-price conversion, SL-first same-bar, prev-bar trailing, position sizing from actual risk distance
- **Risk manager enforced**: max_position_lots=10, min_risk_distance_pips parametric, pip_size per instrument
- **Parameter tuning completed**: Tested 30+ configurations across M5/M3 × 3 pairs × 3 strategies
- **2 branches profitable** (PF > 1.0): EURUSD Sweep (PF 1.01) & GBPUSD ORB (PF 1.11)
- **Risk sizing optimized**: 0.3–0.5% per trade (down from 1%) — critical for survival

### What is NOT done yet
- No separate mature risk engine for daily loss / exposure caps
- No production-grade execution engine
- Not all research strategies have been migrated to the core interface
- No complete MT5 position reconciliation for multi-position and stateful partial-close/trailing lifecycle across restarts
- No repeated live/paper runtime validation on Windows RDP with continuously moving MT5 market data
- SR+EMA V4.1 in core reinforces the suspicion that this static S/R candidate remains weak / bias-prone
- M15 round 1 branch is not robust enough for promotion, especially due to weakness in 2022-2023 despite improvement in 2024-2026
- M15 round 2 long-only improved, with the best candidate so far being `m15_l2_c`, but still regime-sensitive and not yet promotion-worthy
- M15 round 3 quality-filter tests did not beat `m15_l2_c`; the filter reduced DD but also trimmed the edge too much
- M15 round 4 found a stronger recent-regime candidate, `SRSDM15LongRecentStrategy`, with OOS 2025-2026 remaining positive and DD still moderate
- M15 round 5 produced an even stronger recent-regime candidate, `SRSDM15LongRecentV2Strategy`, interesting enough for a separate demo, but fails completely when forced as an all-weather system over 2022-2026
- A separate MT5 paper/demo M15 runtime already exists (`run_paper_trade_mt5_m15.py` and loop) with its own state/journal/config so as not to interfere with the H1 path
- A new research branch has been started for EURUSD session sweep + MSS + FVG, separate from the XAUUSD SR+SD family
- Initial EURUSD research results for Jan-Apr 2026 show M3 already producing early setups/trades, while M5 still has zero entries, so M3 is prioritized for the next refinement
- EURUSD session sweep round 2 shows that mild loosening (`r2_a_looser_sweep`) can add trades without hurting quality, but overly aggressive displacement loosening immediately destroys the edge
- EURUSD session sweep round 3 shows that session expansion is more effective than filter loosening, with Asia+London trigger outperforming Asia-only on M3 Jan-Apr 2026
- Multi-pair round 1 shows that pair expansion does increase frequency, but GBPUSD with copy-paste parameters is still negative and USDJPY has not produced any trades, so the next step must be pair-specific adaptation
- GBPUSD adaptation round 1 successfully turned the GBPUSD branch positive through a tighter sweep (`min_sweep_pips=2.5`) without reducing trade count
- The main basket temporarily consisting of EURUSD + GBPUSD adaptation is still healthy but only averages about 1.43 trades per active week, so it does not yet meet the target of 3 setups per week
- A second pattern branch has been opened for session continuation + FVG as an additional frequency source not dependent solely on the sweep-reversal setup
- Session continuation round 1 on EURUSD M3 is live and has produced clean early trades, though frequency is still low and needs to be tested as part of the basket
- EURUSD dual-pattern basket (sweep + continuation) increases setup variety but still only averages about 1.33 trades per active week, so a single pair is still insufficient for the productivity target
- Main basket v2 (EURUSD sweep + EURUSD continuation + GBPUSD adapted sweep) is the best basket so far, reaching 3 setups in one week but averaging only about 1.86 trades per active week
- After fixing the metrics bug and refreshing continuation, the combined basket temporarily rose to about 2.00 trades per active week and reached the 3-setup target in two weeks, but still does not consistently meet the target
- Adding XAUUSD continuation to the official basket increased it to 17 trades in 8 active weeks, averaging about 2.125 trades per active week, with 3 weeks reaching the 3-setup target. XAU continuation is more promotion-worthy than XAU sweep, but the productivity gap remains
- Light refinement of XAUUSD continuation (looser displacement, smaller FVG, longer expiry) did not change results at all. Preliminary conclusion: this branch's bottleneck is structural, not merely a small threshold issue
- Session ORB (opening-range breakout retest) branch was opened as the third setup family. The EURUSD M3 baseline produced 64 trades, PF ~1.23, return ~+9.08%, DD ~8.72%, and an average of ~4.27 trades per active week. In raw basket frequency terms, adding ORB raised the basket to about 5.4 trades per active week
- Initial overlap review of EURUSD sweep + continuation shows ORB appears genuinely additive, not just massive double-counting. Only about 10.9% of ORB trades occurred on the same day as the existing EURUSD branches, and only about 4.7% of ORB trades actually overlap in position time with other EURUSD branches
- ORB round 2 produced a useful tradeoff: the baseline remains the most aggressive for flow, `r2_b_cleaner_retest` becomes the most balanced candidate for PF (~1.31, 44 trades, ~3.38 trades per active week), while `r2_c_wider_opening_range` provides lower DD (~7.88%) with a weaker PF
- Basket round 6 using ORB `r2_b_cleaner_retest` produced about 61 trades in 13 active weeks, averaging about 4.69 trades per active week, and all 13 active weeks achieved at least 3 trades. This is the first point where the productivity target appears truly solved without having to use the noisiest ORB baseline
- Realism review round 6 shows this basket remains strong even after simple execution rules are applied. With a global one-position rule, the basket still retains 58 of 61 trades (~95.1%) and averages about 4.46 trades per active week. With a one-position-per-symbol rule, the basket retains 60 of 61 trades (~98.4%). This alleviates concerns that the basket only looks good due to unrealistic execution conflicts
- Priority review shows the basket's actual conflicts are very small (only 3 observed pair overlaps). Simple rules are sufficient: one position per symbol, branch priority for conflicts on the same symbol, and cross-symbol overlap is still permitted. The current priority order is: EURUSD sweep > EURUSD continuation > XAUUSD continuation > GBPUSD adapted sweep > EURUSD ORB r2_b > GBPUSD continuation
- Risk sizing review leads to a simple tier model rather than a flat 1% for all branches. Initial recommendation: EURUSD sweep 1.00%, EURUSD continuation 0.75%, XAUUSD continuation 0.75%, GBPUSD adapted sweep 0.60%, EURUSD ORB r2_b 0.50%, GBPUSD continuation 0.25%. The key takeaway: ORB remains the flow engine, but should not dominate the risk budget like the cleanest branches
- Final portfolio spec v1 for the session basket has been written in `docs/session_basket_portfolio_spec_v1.md`. This is now the source of truth for the demo runner implementation: branch set, priority order, one-position-per-symbol rule, cross-symbol overlap policy, risk tiers, and initial guardrails
- Initial demo basket v1 runner implementation has been created: `run_session_basket_demo_mt5.py` + `run_session_basket_demo_mt5_loop.py` + sample Windows config + launcher bat. This runner already implements branch priority, risk tier per branch (via metadata risk override), and one-position-per-symbol for multi-symbol paper-demo on MT5

## Architecture decision

### Chosen architecture now
**Research-first modular layered architecture**

This means:
- research scripts remain separate and preserved
- reusable core logic is gradually moved to `src/xauusd_trading/`
- not yet using full clean architecture because the reusable domain layer is not yet mature enough
- not yet using ML layer / LLM layer as core components

### Why this is realistic
Because the main bottleneck right now is not a lack of AI.
The main bottleneck is:
1. finding genuine alpha
2. avoiding research bias
3. making risk + execution safe
4. only then building more sophisticated automation

## Folder status

### Stable folders
- `research/experiments/` → historical strategy experiments
- `research/walkforward/` → no-look-ahead validation
- `research/cross_asset/` → transferability checks
- `docs/` → architecture and design notes
- `src/xauusd_trading/` → target reusable core

## Active Portfolio (Session Basket v1)

### Active strategies (3 files, 6 branches)
| Branch ID | Strategy File | Pair | Risk Tier |
|-----------|--------------|------|-----------|
| `eurusd_sweep` | `eurusd_session_sweep.py` | EURUSD | 1.00% |
| `eurusd_continuation` | `session_continuation.py` | EURUSD | 0.75% |
| `eurusd_orb` | `session_orb_retest.py` | EURUSD | 0.50% |
| `gbpusd_sweep` | `eurusd_session_sweep.py` (adapted) | GBPUSD | 0.60% |
| `gbpusd_continuation` | `session_continuation.py` | GBPUSD | 0.25% |
| `xauusd_continuation` | `session_continuation.py` | XAUUSD | 0.75% |

### Branch priority (same-symbol conflict resolution)
1. `eurusd_sweep` → 2. `eurusd_continuation` → 3. `xauusd_continuation` → 4. `gbpusd_sweep` → 5. `eurusd_orb` → 6. `gbpusd_continuation`

### Active runner scripts
- `run_session_basket_demo_mt5.py` / `run_session_basket_demo_mt5_loop.py` — MT5 paper/demo
- `run_session_basket_execution_mt5.py` / `run_session_basket_execution_mt5_loop.py` — MT5 execution (dry-run default)

### Archived (legacy)
- `src/xauusd_trading/strategies/_archive_legacy/` — sr_sd_v35, sr_sd_v35_short, sr_ema_v41, tf001, m15_sr_sd
- `_archive_legacy_runners/` — run_paper_trade, run_paper_trade_mt5, run_mt5_execution, run_paper_trade_mt5_m15 (+ loops)

## Recommended next phases

### Phase A, cleanup foundation
- create shared path/config module
- create unified data loader
- create unified indicator/feature module
- create minimal strategy interface

### Phase B, core engine
- one reusable backtest engine
- one trade model / position model
- one metrics/report module

### Phase C, safety
- risk caps
- drawdown guard
- kill switch
- paper-trade execution adapter

### Phase D, optional intelligence
- ML only if it adds real edge
- LLM only for explanation, regime annotation, or operator assistance

## Hard rules
- no look-ahead
- no hidden future leakage
- no direct jump to live before paper validation
- no overengineering before reusable core exists
- every next phase must pass audit before expanding scope
