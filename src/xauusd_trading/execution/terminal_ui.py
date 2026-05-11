"""Beautiful terminal output for XAUUSD Trading Bot.

Provides ANSI-colored, structured dashboard output for loop heartbeat,
signal events, and trade decisions — replacing raw JSON dumps.

Windows support: auto-enables Virtual Terminal Processing (ANSI)
via ctypes. Falls back to plain text if unavailable.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

# ── Windows ANSI Support ──────────────────────────────────────────────
def _enable_windows_ansi() -> bool:
    """Enable Virtual Terminal Processing on Windows 10+ cmd.exe.
    
    Without this, ANSI escape codes print as raw text like [94m[1m...
    Returns True if successfully enabled.
    """
    if sys.platform != "win32":
        return True  # Not Windows, assume ANSI works
    
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        
        # STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(-11)
        if handle == -1:
            return False
        
        # Get current console mode
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        VT_PROCESSING = 0x0004
        if mode.value & VT_PROCESSING:
            return True  # Already enabled
        
        # Try to enable it
        new_mode = mode.value | VT_PROCESSING
        if kernel32.SetConsoleMode(handle, new_mode):
            return True
        
        # Some older Windows 10 builds also need DISABLE_NEWLINE_AUTO_RETURN
        # ENABLE_PROCESSED_OUTPUT = 0x0001
        new_mode = new_mode | 0x0001
        return bool(kernel32.SetConsoleMode(handle, new_mode))
    except Exception:
        return False


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r'\033\[[0-9;]*m', '', text)


# ── ANSI Colors ───────────────────────────────────────────────────────
_NO_COLOR = os.getenv("NO_COLOR") or os.getenv("TERM") == "dumb"
_ANSI_ENABLED = False


def _supports_color() -> bool:
    if _NO_COLOR:
        return False
    if sys.platform == "win32":
        return _enable_windows_ansi()
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# Enable ANSI on Windows ASAP (before any print)
_ANSI_ENABLED = _supports_color()

if _ANSI_ENABLED:
    R = "\033[91m"    # red
    G = "\033[92m"    # green
    Y = "\033[93m"    # yellow
    B = "\033[94m"    # blue
    C = "\033[96m"    # cyan
    W = "\033[97m"    # white bright
    DIM = "\033[2m"   # dim
    BOLD = "\033[1m"
    RESET = "\033[0m"
else:
    R = G = Y = B = C = W = DIM = BOLD = RESET = ""


# ── Helpers ────────────────────────────────────────────────────────────
def _hr(char: str = "─", width: int = 52) -> str:
    return f"{DIM}{char * width}{RESET}"


def _label(key: str, value: str, *, color: str = "") -> str:
    return f"  {DIM}{key}:{RESET} {color}{value}{RESET}"


def _side_icon(side: str) -> str:
    if side == "LONG":
        return f"{G}▲ LONG{RESET}"
    if side == "SHORT":
        return f"{R}▼ SHORT{RESET}"
    return side


def _branch_icon(branch_id: str) -> str:
    icons = {
        "eurusd_sweep": "⚡",
        "gbpusd_orb": "📊",
        "eurusd_orb": "📊",
        "xauusd_continuation": "🥇",
    }
    return f"{icons.get(branch_id, '📈')} {branch_id}"


def _pnl_color(val: float) -> str:
    if val > 0:
        return G
    if val < 0:
        return R
    return W


def _reason_icon(reason_code: str) -> str:
    """Color-code the reason for no signal."""
    normal = {"NO_BREAKOUT", "NO_FVG", "NO_SIDE_READY", "NO_RECENT_SWEEP",
              "OPENING_RANGE_STILL_BUILDING", "ENTRY_EXPIRED", "NO_FIRST_RETRACE",
              "INSUFFICIENT_DATA", "SPREAD_TOO_WIDE", "OUTSIDE_SESSION"}
    if reason_code in normal:
        return f"{Y}⏳{RESET}"
    if "SIGNAL" in reason_code or "READY" in reason_code:
        return f"{G}✅{RESET}"
    return f"{DIM}·{RESET}"


# ── Dashboard: Loop Heartbeat ──────────────────────────────────────────
def format_heartbeat(
    *,
    iteration: int,
    timestamp: str,
    mode: str = "DEMO",
    accepted: int = 0,
    rejected: int = 0,
    open_positions: int = 0,
    live_actions: int = 0,
    telegram_sent: int = 0,
    orders_filled: int = 0,
    orders_rejected: int = 0,
    session_signals: int | None = None,
    session_orders_filled: int | None = None,
    session_orders_rejected: int | None = None,
    session_telegram_sent: int | None = None,
    debug_summary: str = "",
    symbols: dict[str, dict] | None = None,
    branch_debugs: list[dict] | None = None,
    balance: float | None = None,
    peak_balance: float | None = None,
    closed_trades: int | None = None,
) -> str:
    """Format a single heartbeat iteration as a pretty dashboard."""
    lines: list[str] = []

    # Header
    mode_color = G if mode == "LIVE" else C
    lines.append(_hr("━"))
    lines.append(f"  {BOLD}🤖 XAUUSD BOT{RESET}  │  iter {BOLD}{iteration}{RESET}  │  {mode_color}{BOLD}{mode}{RESET}  │  {DIM}{timestamp[:19]}{RESET}")
    lines.append(_hr("━"))

    # Summary row — per current iteration
    sig_color = G if accepted > 0 else DIM
    pos_color = G if open_positions > 0 else DIM
    fill_color = G if orders_filled > 0 else DIM
    reject_color = R if orders_rejected > 0 else DIM
    lines.append(
        f"  {sig_color}Signals now: {accepted}{RESET}  "
        f"│  {fill_color}Filled now: {orders_filled}{RESET}  "
        f"│  {reject_color}Rejected now: {orders_rejected}{RESET}  "
        f"│  {pos_color}Open: {open_positions}{RESET}  "
        f"│  📨 TG now: {telegram_sent}"
    )

    # Session cumulative row — since this loop process started
    if any(v is not None for v in (session_signals, session_orders_filled, session_orders_rejected, session_telegram_sent)):
        lines.append(
            f"  {DIM}Session:{RESET} "
            f"signals={session_signals or 0}  "
            f"filled={session_orders_filled or 0}  "
            f"rejected={session_orders_rejected or 0}  "
            f"telegram={session_telegram_sent or 0}"
        )

    # Balance row (if available)
    if balance is not None:
        dd = ((peak_balance or balance) - balance) / (peak_balance or balance) * 100 if (peak_balance or balance) > 0 else 0
        dd_color = G if dd < 3 else Y if dd < 8 else R
        lines.append(
            f"  💰 Balance: {BOLD}${balance:,.2f}{RESET}  "
            f"│  Peak: ${peak_balance:,.2f}  "
            f"│  {dd_color}DD: {dd:.1f}%{RESET}  "
            f"│  Trades: {closed_trades or 0}"
        )

    # Branch status
    if branch_debugs:
        lines.append(f"")
        lines.append(f"  {BOLD}Branches:{RESET}")
        for bd in branch_debugs:
            bid = bd.get("branch_id", "?")
            rc = bd.get("reason_code", "?")
            has_sig = bd.get("has_signal", False)
            sym = bd.get("symbol", "?")
            icon = f"{G}✅{RESET}" if has_sig else _reason_icon(rc)
            branch_line = f"    {icon} {_branch_icon(bid):30s} {sym:8s} {rc}"
            # If has signal, show entry info
            if has_sig:
                side = bd.get("side", "?")
                entry = bd.get("entry_price")
                if entry is not None:
                    entry_str = f"{float(entry):.5f}" if float(entry) < 100 else f"{float(entry):.2f}"
                    branch_line += f"  │  {_side_icon(side)} @ {entry_str}"
            lines.append(branch_line)

    # Symbol positions (for execution mode)
    if symbols:
        lines.append(f"")
        lines.append(f"  {BOLD}Positions:{RESET}")
        for sym, info in symbols.items():
            action = info.get("action", "HOLD")
            reason = info.get("reason", "")
            positions = info.get("positions", 0)
            action_color = G if action != "HOLD" else DIM
            lines.append(
                f"    {action_color}{action:16s}{RESET} {sym:8s} "
                f"│  pos={positions}  │  {DIM}{reason}{RESET}"
            )

    lines.append(_hr("━"))
    return "\n".join(lines)


# ── Signal Event ───────────────────────────────────────────────────────
def format_signal_event(event: dict[str, Any]) -> str:
    """Format a signal/trade event for terminal display."""
    event_type = event.get("type", "EVENT")

    if event_type == "OPEN":
        pos = event.get("position", {})
        side = pos.get("side", "?")
        symbol = pos.get("symbol", "?")
        entry = float(pos.get("entry_price", 0))
        sl = float(pos.get("stop_loss", 0))
        tp = float(pos.get("take_profit", 0))
        pip_size = float(pos.get("pip_size", 0.0001))
        branch = pos.get("strategy", "?")
        risk_pips = abs(entry - sl) / pip_size if pip_size > 0 else 0
        rr = abs(entry - tp) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        fmt = f".5f" if entry < 100 else f".2f"

        return (
            f"{_hr('━')}\n"
            f"  {G}{BOLD}📥 TRADE OPENED{RESET}\n"
            f"  {_side_icon(side)}  {BOLD}{symbol}{RESET}\n"
            f"  {_branch_icon(branch)}\n"
            f"  {_label('Entry', f'{entry:{fmt}}', color=C)}\n"
            f"  {_label('SL', f'{sl:{fmt}}', color=R)}  ({risk_pips:.1f} pips)\n"
            f"  {_label('TP', f'{tp:{fmt}}', color=G)}  (R:R {rr:.1f})\n"
            f"  {_label('Time', pos.get('entry_time', '?'))}\n"
            f"{_hr('━')}"
        )

    if event_type == "CLOSE":
        pnl = float(event.get("pnl_currency", 0))
        pnl_pct = float(event.get("pnl_pct", 0))
        pnl_color = _pnl_color(pnl)
        side = event.get("side", "?")
        symbol = event.get("symbol", "?")
        equity = float(event.get("equity_after", 0))

        return (
            f"{_hr('━')}\n"
            f"  {_pnl_color(pnl)}{BOLD}{'🟢' if pnl >= 0 else '🔴'} TRADE CLOSED{RESET}\n"
            f"  {_side_icon(side)}  {BOLD}{symbol}{RESET}\n"
            f"  {_label('PnL', f'{pnl:+.2f} ({pnl_pct:+.2f}%)', color=pnl_color)}\n"
            f"  {_label('Exit', event.get('exit_reason', '?'))}\n"
            f"  {_label('Equity', f'${equity:,.2f}')}\n"
            f"{_hr('━')}"
        )

    if event_type == "EXECUTION_DECISION":
        action = event.get("action", "?")
        reason = event.get("reason", "?")
        symbol = event.get("symbol", "?")
        intent = event.get("intent") or {}
        send_result = event.get("send_result") or {}
        broker_positions = event.get("broker_positions") or []
        metadata = event.get("metadata") or {}
        branch_debugs = metadata.get("branch_debugs") or []

        if action == "OPEN" and intent:
            side = intent.get("side", "?")
            entry = float(intent.get("entry_price", 0))
            sl = float(intent.get("stop_loss", 0))
            tp = float(intent.get("take_profit", 0))
            volume = float(intent.get("volume", 0))
            intent_meta = intent.get("metadata") or {}
            pip_size = float(intent_meta.get("pip_size", 0.0001))
            branch_id = intent_meta.get("branch_id", "?")
            risk_pct = float(intent_meta.get("risk_per_trade", 0))
            rr = abs(entry - tp) / abs(entry - sl) if abs(entry - sl) > 0 else 0
            risk_pips = abs(entry - sl) / pip_size if pip_size > 0 else 0
            sent = send_result.get("sent", False)
            retcode = send_result.get("retcode", -1)
            mode = send_result.get("mode", "?")
            fmt = f".5f" if entry < 100 else f".2f"
            status = f"{G}✅ FILLED{RESET}" if sent else f"{R}❌ REJECTED (retcode {retcode}){RESET}"

            return (
                f"{_hr('━')}\n"
                f"  {BOLD}📥 LIVE ORDER{RESET}\n"
                f"  {_side_icon(side)}  {BOLD}{symbol}{RESET}  ×{volume:.2f} lot\n"
                f"  {_branch_icon(branch_id)}  (risk {risk_pct*100:.1f}%)\n"
                f"  {_label('Entry', f'{entry:{fmt}}', color=C)}\n"
                f"  {_label('SL', f'{sl:{fmt}}', color=R)}  ({risk_pips:.1f} pips)\n"
                f"  {_label('TP', f'{tp:{fmt}}', color=G)}  (R:R {rr:.1f})\n"
                f"  {status}  [{mode}]\n"
                f"{_hr('━')}"
            )

        if action in ("HOLD", "MANAGE_POSITION", "SYNC_SLTP", "REVERSE"):
            # Position summary
            pos_lines = []
            for pos in broker_positions:
                p_side = pos.get("side", "?")
                p_vol = float(pos.get("volume", 0))
                p_pnl = float(pos.get("profit", 0))
                pnl_icon = f"{G}🟢{RESET}" if p_pnl >= 0 else f"{R}🔴{RESET}"
                pos_lines.append(f"    {pnl_icon} {_side_icon(p_side)} ×{p_vol:.2f}  PnL: {_pnl_color(p_pnl)}{p_pnl:+.2f}{RESET}")

            # Branch debugs
            debug_lines = []
            for bd in branch_debugs:
                bid = bd.get("branch_id", "?")
                rc = bd.get("reason_code", "?")
                has_sig = bd.get("has_signal", False)
                icon = f"{G}✅{RESET}" if has_sig else _reason_icon(rc)
                debug_lines.append(f"    {icon} {_branch_icon(bid)}: {rc}")

            action_color = Y if action == "HOLD" else C
            lines = [
                _hr("━"),
                f"  {action_color}{BOLD}⏸ {action}{RESET}  │  {DIM}{reason}{RESET}",
                f"  📊 {BOLD}{symbol}{RESET}",
            ]
            if pos_lines:
                lines.append(f"  {BOLD}Positions:{RESET}")
                lines.extend(pos_lines)
            if debug_lines:
                lines.append(f"  {BOLD}Branches:{RESET}")
                lines.extend(debug_lines)
            lines.append(_hr("━"))
            return "\n".join(lines)

    # Fallback
    return f"  ⚙️ {event_type}"


# ── Paper Trade Scan Summary ───────────────────────────────────────────
def format_paper_scan(result: dict) -> str:
    """Format paper trade scan result for terminal."""
    lines: list[str] = []
    scan_time = result.get("scan_time", "?")[:19]

    lines.append(_hr("━"))
    lines.append(f"  {BOLD}🔍 PAPER TRADE SCAN{RESET}  │  {DIM}{scan_time}{RESET}")
    lines.append(_hr("━"))

    total = result.get("total_branches", 0)
    found = result.get("signals_found", 0)
    accepted = result.get("accepted_signals", 0)
    lines.append(f"  Branches: {total}  │  Signals: {found}  │  Accepted: {G}{accepted}{RESET}")

    # Per-branch status
    results = result.get("results", {})
    if results:
        lines.append(f"")
        lines.append(f"  {BOLD}Branch Status:{RESET}")
        for bid, info in results.items():
            has_sig = info.get("has_signal", False)
            rc = info.get("debug_reason", "?")
            sym = info.get("symbol", "?")
            bars = info.get("bars", "?")
            icon = f"{G}✅{RESET}" if has_sig else _reason_icon(rc)
            line = f"    {icon} {_branch_icon(bid):30s} {sym:8s} bars={bars}  {rc}"
            if has_sig:
                side = info.get("side", "?")
                entry = info.get("entry_price")
                if entry is not None:
                    entry_val = float(entry)
                    fmt = f".5f" if entry_val < 100 else f".2f"
                    line += f"  │  {_side_icon(side)} @ {entry_val:{fmt}}"
                rr = info.get("rr_ratio")
                if rr is not None:
                    line += f"  R:R={rr}"
                risk_pips = info.get("risk_pips")
                if risk_pips is not None:
                    line += f"  risk={risk_pips}pips"
            lines.append(line)

    # Accepted signals detail
    accepted_list = result.get("accepted", [])
    if accepted_list:
        lines.append(f"")
        lines.append(f"  {G}{BOLD}✅ ACCEPTED:{RESET}")
        for sig in accepted_list:
            side = sig.get("side", "?")
            symbol = sig.get("symbol", "?")
            entry = float(sig.get("entry_price", 0))
            sl = float(sig.get("stop_loss", 0))
            tp = float(sig.get("take_profit", 0))
            rr = sig.get("rr_ratio", "?")
            fmt = f".5f" if entry < 100 else f".2f"
            lines.append(
                f"    {_side_icon(side)}  {BOLD}{symbol}{RESET}\n"
                f"      Entry: {C}{entry:{fmt}}{RESET}  "
                f"SL: {R}{sl:{fmt}}{RESET}  "
                f"TP: {G}{tp:{fmt}}{RESET}  "
                f"R:R {rr}"
            )
    else:
        lines.append(f"  {DIM}No signals accepted.{RESET}")

    lines.append(_hr("━"))
    return "\n".join(lines)


# ── One-shot execution summary ─────────────────────────────────────────
def format_execution_summary(payload: dict) -> str:
    """Format run_once() output as pretty terminal summary instead of raw JSON."""
    lines: list[str] = []
    mode = payload.get("mode", "?")
    mode_color = G if mode == "LIVE" else C

    lines.append(_hr("━"))
    lines.append(f"  {BOLD}🤖 EXECUTION RUN{RESET}  │  {mode_color}{BOLD}{mode}{RESET}")
    lines.append(_hr("━"))

    # Risk config
    risk = payload.get("risk", {})
    lines.append(
        f"  Risk: {risk.get('risk_per_trade_default', 0)*100:.1f}%  "
        f"│  MaxDD: {risk.get('max_drawdown_pct', 0):.0f}%  "
        f"│  MinBal: ${risk.get('min_balance', 0):,.0f}"
    )

    # Guards (execution mode)
    guards = payload.get("guards")
    if guards:
        lines.append(
            f"  Guards: maxPos={guards.get('max_live_positions_total')}  "
            f"│  perSym={guards.get('max_live_positions_per_symbol')}  "
            f"│  maxOrders={guards.get('max_new_orders_per_run')}"
        )

    # Branch debugs
    branch_debugs = payload.get("branch_debugs", [])
    if branch_debugs:
        lines.append(f"")
        lines.append(f"  {BOLD}Branches:{RESET}")
        for bd in branch_debugs:
            bid = bd.get("branch_id", "?")
            rc = bd.get("reason_code", "?")
            has_sig = bd.get("has_signal", False)
            sym = bd.get("symbol", "?")
            icon = f"{G}✅{RESET}" if has_sig else _reason_icon(rc)
            line = f"    {icon} {_branch_icon(bid):30s} {sym:8s} {rc}"
            if has_sig:
                side = bd.get("side", "?")
                entry = bd.get("entry_price")
                if entry is not None:
                    entry_val = float(entry)
                    fmt = f".5f" if entry_val < 100 else f".2f"
                    line += f"  │  {_side_icon(side)} @ {entry_val:{fmt}}"
            lines.append(line)

    # Accepted signals
    accepted = payload.get("accepted_signals", [])
    if accepted:
        lines.append(f"")
        lines.append(f"  {G}{BOLD}✅ ACCEPTED SIGNALS:{RESET}")
        for sig in accepted:
            side = sig.get("side", "?")
            symbol = sig.get("symbol", "?")
            entry = float(sig.get("entry_price", 0))
            sl = float(sig.get("stop_loss", 0))
            tp = float(sig.get("take_profit", 0))
            branch = sig.get("branch_id", "?")
            risk_pct = float(sig.get("risk_per_trade", 0))
            fmt = f".5f" if entry < 100 else f".2f"
            lines.append(
                f"    {_side_icon(side)}  {BOLD}{symbol}{RESET}  [{_branch_icon(branch)}]  risk={risk_pct*100:.1f}%\n"
                f"      Entry: {C}{entry:{fmt}}{RESET}  "
                f"SL: {R}{sl:{fmt}}{RESET}  "
                f"TP: {G}{tp:{fmt}}{RESET}"
            )

    # Per-symbol decisions
    per_symbol = payload.get("per_symbol", {})
    if per_symbol:
        lines.append(f"")
        lines.append(f"  {BOLD}Per-Symbol:{RESET}")
        for sym, info in per_symbol.items():
            decision = info.get("decision", {})
            action = decision.get("action", "?")
            reason = decision.get("reason", "?")
            action_color = G if action == "OPEN" else C if action != "HOLD" else DIM
            balance = info.get("balance")
            pos_count = len(info.get("open_positions", []) or info.get("broker_positions", []))
            line = f"    {action_color}{action:16s}{RESET} {sym:8s}  │  {DIM}{reason}{RESET}  │  pos={pos_count}"
            if balance is not None:
                line += f"  │  💰 ${balance:,.2f}"
            lines.append(line)

            # Show events (trades opened/closed)
            events = info.get("events", [])
            for ev in events:
                ev_type = ev.get("type", "?")
                if ev_type == "OPEN":
                    pos = ev.get("position", {})
                    side = pos.get("side", "?")
                    entry = float(pos.get("entry_price", 0))
                    fmt = f".5f" if entry < 100 else f".2f"
                    lines.append(f"      {G}▶ OPENED{RESET} {_side_icon(side)} @ {C}{entry:{fmt}}{RESET}")
                elif ev_type == "CLOSE":
                    pnl = float(ev.get("pnl_currency", 0))
                    pnl_pct = float(ev.get("pnl_pct", 0))
                    lines.append(f"      {_pnl_color(pnl)}■ CLOSED{RESET} PnL: {_pnl_color(pnl)}{pnl:+.2f} ({pnl_pct:+.2f}%){RESET}")

    # Telegram
    tg = payload.get("telegram_alerts_sent", 0)
    if tg:
        lines.append(f"  📨 Telegram alerts sent: {tg}")

    lines.append(_hr("━"))
    return "\n".join(lines)


# ── Startup Banner ──────────────────────────────────────────────────────
BANNER = f"""{B}{BOLD}
  ╔══════════════════════════════════════════╗
  ║   🤖 XAUUSD SESSION BASKET BOT v1.0     ║
  ║   ─────────────────────────────────────  ║
  ║   EURUSD │ GBPUSD │ XAUUSD              ║
  ║   Sweep  │  ORB   │ Continuation        ║
  ╚══════════════════════════════════════════╝{RESET}"""
