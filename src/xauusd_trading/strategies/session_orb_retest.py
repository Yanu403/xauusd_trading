from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xauusd_trading.features.indicators import add_atr_column, add_session_columns
from xauusd_trading.models.trading import TradeSignal
from xauusd_trading.strategies.base import Strategy


@dataclass(slots=True)
class SessionORBRetestStrategy(Strategy):
    """Opening-range breakout retest for London / New York sessions.

    Goal:
    - capture session expansion days that do not begin with a clean sweep reversal
    - require objective breakout strength
    - enter only on the first retest of the broken opening-range boundary
    """

    name: str = 'session_orb_retest'
    execution_timeframe: str = 'M3'
    london_start_hour_utc: int = 7
    london_end_hour_utc: int = 16   # Extended: was 11
    new_york_start_hour_utc: int = 12
    new_york_end_hour_utc: int = 21  # Extended: was 16
    opening_range_bars: int = 4
    displacement_atr_multiple: float = 1.3
    breakout_buffer_pips: float = 1.0
    retest_tolerance_pips: float = 0.5
    max_spread_pips: float = 1.5
    spread_points_per_pip: float = 10.0
    pip_size: float = 0.0001
    lot_size: float = 100_000.0  # Forex default: 1 lot = 100,000 units. Override to 100 for XAUUSD.
    breakout_lookback_bars: int = 8
    entry_expiry_bars: int = 8
    max_bars_hold: int = 24
    rr_target: float = 2.0
    stop_buffer_pips: float = 0.5

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        result = add_session_columns(result, timezone_offset_hours=0)
        result = add_atr_column(result, period=14, column_name='atr14')
        result['date_utc'] = result.index.date.astype(str)
        result['session_name'] = pd.NA
        result.loc[result['hour_utc'].between(self.london_start_hour_utc, self.london_end_hour_utc - 1), 'session_name'] = 'london'
        result.loc[result['hour_utc'].between(self.new_york_start_hour_utc, self.new_york_end_hour_utc - 1), 'session_name'] = 'new_york'
        result['is_trade_window'] = result['session_name'].notna().astype(int)
        result['session_id'] = (result['date_utc'] + '_' + result['session_name'].fillna('off')).astype(str)
        result['session_bar_index'] = result.groupby('session_id').cumcount()

        # ── Opening range computation (look-ahead-free) ──────────────────────
        # cummax / cummin are cumulative (forward-only) within each session
        # group; ffill propagates the final OR value to later bars without
        # peeking at future data.  No shift(-N), rolling(center=True), or
        # future-referencing groupby transforms are used anywhere here.
        in_opening_range = (result['is_trade_window'] == 1) & (result['session_bar_index'] < self.opening_range_bars)
        result['opening_range_high_seed'] = result['high'].where(in_opening_range)
        result['opening_range_low_seed'] = result['low'].where(in_opening_range)
        result['opening_range_high'] = result.groupby('session_id')['opening_range_high_seed'].cummax()
        result['opening_range_low'] = result.groupby('session_id')['opening_range_low_seed'].cummin()
        result['opening_range_high'] = result.groupby('session_id')['opening_range_high'].ffill()
        result['opening_range_low'] = result.groupby('session_id')['opening_range_low'].ffill()
        result['opening_range_size_pips'] = (result['opening_range_high'] - result['opening_range_low']) / self.pip_size

        result['body_size'] = (result['close'] - result['open']).abs()
        result['body_atr_ratio'] = result['body_size'] / result['atr14'].clip(lower=self.pip_size)
        result['bullish_displacement'] = ((result['close'] > result['open']) & (result['body_atr_ratio'] >= self.displacement_atr_multiple)).astype(int)
        result['bearish_displacement'] = ((result['close'] < result['open']) & (result['body_atr_ratio'] >= self.displacement_atr_multiple)).astype(int)
        if 'spread' in result.columns:
            result['spread_pips'] = result['spread'] / self.spread_points_per_pip
        else:
            result['spread_pips'] = pd.NA
        return result

    def _spread_ok(self, row: pd.Series) -> bool:
        spread = row.get('spread_pips')
        if spread is None or pd.isna(spread):
            return True
        return float(spread) <= self.max_spread_pips

    def _find_breakout_index(self, df: pd.DataFrame, index: int, side: str) -> int | None:
        current_row = df.iloc[index]
        current_session = current_row.get('session_id')
        start = max(0, index - self.breakout_lookback_bars)
        for candidate in range(index - 1, start - 1, -1):
            row = df.iloc[candidate]
            if row.get('session_id') != current_session:
                continue
            if int(row.get('is_trade_window', 0)) != 1:
                continue
            if int(row.get('session_bar_index', 0)) < self.opening_range_bars:
                continue
            or_high = row.get('opening_range_high')
            or_low = row.get('opening_range_low')
            if pd.isna(or_high) or pd.isna(or_low):
                continue
            if side == 'LONG':
                if int(row.get('bullish_displacement', 0)) == 1 and float(row['close']) > float(or_high) + self.breakout_buffer_pips * self.pip_size:
                    return candidate
            else:
                if int(row.get('bearish_displacement', 0)) == 1 and float(row['close']) < float(or_low) - self.breakout_buffer_pips * self.pip_size:
                    return candidate
        return None

    def _entry_from_retest(self, row: pd.Series, side: str, level: float) -> float | None:
        tolerance = self.retest_tolerance_pips * self.pip_size
        low = float(row['low'])
        high = float(row['high'])
        close = float(row['close'])
        if side == 'LONG':
            if low <= level + tolerance and high >= level:
                return max(level, min(close, level + tolerance))
            return None
        if high >= level - tolerance and low <= level:
            return min(level, max(close, level - tolerance))
        return None

    def _build_signal(self, df: pd.DataFrame, index: int, breakout_index: int, side: str, entry_price: float, level: float) -> TradeSignal | None:
        breakout_row = df.iloc[breakout_index]
        if side == 'LONG':
            stop_loss = float(breakout_row['low']) - self.stop_buffer_pips * self.pip_size
            if stop_loss >= entry_price:
                return None
        else:
            stop_loss = float(breakout_row['high']) + self.stop_buffer_pips * self.pip_size
            if stop_loss <= entry_price:
                return None
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance < self.pip_size:
            return None
        take_profit = entry_price + self.rr_target * risk_distance if side == 'LONG' else entry_price - self.rr_target * risk_distance
        return TradeSignal(
            index=index,
            timestamp=df.index[index],
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_hold_bars=self.max_bars_hold,
            metadata={
                'strategy': self.name,
                'execution_timeframe': self.execution_timeframe,
                'breakout_index': breakout_index,
                'opening_range_level': level,
                'rr_target': self.rr_target,
                'pip_size': self.pip_size,
                'spread_points_per_pip': self.spread_points_per_pip,
                'lot_size': self.lot_size,
            },
        )

    def generate_signal(self, df: pd.DataFrame, index: int) -> TradeSignal | None:
        if index < max(self.opening_range_bars + 2, 20):
            return None
        row = df.iloc[index]
        if int(row.get('is_trade_window', 0)) != 1:
            return None
        if int(row.get('session_bar_index', 0)) < self.opening_range_bars:
            return None
        if not self._spread_ok(row):
            return None

        for side in ('LONG', 'SHORT'):
            breakout_index = self._find_breakout_index(df, index, side)
            if breakout_index is None:
                continue
            if index <= breakout_index or index - breakout_index > self.entry_expiry_bars:
                continue
            for prior in range(breakout_index + 1, index):
                prior_row = df.iloc[prior]
                level = float(prior_row['opening_range_high']) if side == 'LONG' else float(prior_row['opening_range_low'])
                if self._entry_from_retest(prior_row, side, level) is not None:
                    breakout_index = None
                    break
            if breakout_index is None:
                continue
            level = float(row['opening_range_high']) if side == 'LONG' else float(row['opening_range_low'])
            entry_price = self._entry_from_retest(row, side, level)
            if entry_price is None:
                continue
            signal = self._build_signal(df, index, breakout_index, side, entry_price, level)
            if signal is not None:
                return signal
        return None

    def debug_signal(self, df: pd.DataFrame, index: int) -> dict:
        min_index = max(self.opening_range_bars + 2, 20)
        if index < min_index:
            return self._debug_payload(False, 'WARMUP_NOT_REACHED', 'Not enough bars to evaluate ORB retest setup', details={'index': index, 'min_index': min_index})

        row = df.iloc[index]
        if int(row.get('is_trade_window', 0)) != 1:
            return self._debug_payload(False, 'OUTSIDE_TRADE_WINDOW', 'Current bar is outside London/New York trade window', details={'hour_utc': int(row.get('hour_utc', -1))})
        if int(row.get('session_bar_index', 0)) < self.opening_range_bars:
            return self._debug_payload(False, 'OPENING_RANGE_STILL_BUILDING', 'Opening range is still forming', details={'session_bar_index': int(row.get('session_bar_index', -1)), 'opening_range_bars': self.opening_range_bars})
        if not self._spread_ok(row):
            spread = row.get('spread_pips')
            return self._debug_payload(False, 'SPREAD_TOO_WIDE', 'Spread is above the allowed threshold', details={'spread_pips': None if pd.isna(spread) else float(spread), 'max_spread_pips': self.max_spread_pips})

        side_debugs: list[dict] = []
        for side in ('LONG', 'SHORT'):
            breakout_index = self._find_breakout_index(df, index, side)
            if breakout_index is None:
                side_debugs.append(self._debug_payload(False, 'NO_BREAKOUT', 'No qualifying opening-range breakout found recently', side=side))
                continue
            if index <= breakout_index:
                side_debugs.append(self._debug_payload(False, 'BREAKOUT_NOT_CONFIRMED', 'Current bar is not after the breakout bar yet', side=side, details={'breakout_index': breakout_index}))
                continue
            if index - breakout_index > self.entry_expiry_bars:
                side_debugs.append(self._debug_payload(False, 'ENTRY_EXPIRED', 'Breakout is too old for a valid retest entry', side=side, details={'breakout_index': breakout_index, 'bars_since_breakout': index - breakout_index, 'entry_expiry_bars': self.entry_expiry_bars}))
                continue
            prior_retest_found = False
            for prior in range(breakout_index + 1, index):
                prior_row = df.iloc[prior]
                level = float(prior_row['opening_range_high']) if side == 'LONG' else float(prior_row['opening_range_low'])
                if self._entry_from_retest(prior_row, side, level) is not None:
                    prior_retest_found = True
                    break
            if prior_retest_found:
                side_debugs.append(self._debug_payload(False, 'RETEST_ALREADY_USED', 'A prior retest already touched the breakout level', side=side, details={'breakout_index': breakout_index}))
                continue
            level = float(row['opening_range_high']) if side == 'LONG' else float(row['opening_range_low'])
            entry_price = self._entry_from_retest(row, side, level)
            if entry_price is None:
                side_debugs.append(self._debug_payload(False, 'NO_RETEST', 'Price has not retested the opening-range boundary yet', side=side, details={'breakout_index': breakout_index, 'opening_range_level': level}))
                continue
            signal = self._build_signal(df, index, breakout_index, side, entry_price, level)
            if signal is not None:
                return self._debug_signal_ready(signal)
            side_debugs.append(self._debug_payload(False, 'INVALID_RISK_GEOMETRY', 'Setup exists but stop-loss / take-profit geometry is invalid', side=side, details={'breakout_index': breakout_index, 'opening_range_level': level, 'entry_price': entry_price}))

        return self._combine_side_debugs(side_debugs)
