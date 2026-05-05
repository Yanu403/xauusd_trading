from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xauusd_trading.features.indicators import add_atr_column, add_session_columns
from xauusd_trading.models.trading import TradeSignal
from xauusd_trading.strategies.base import Strategy


@dataclass(slots=True)
class SessionContinuationFVGStrategy(Strategy):
    """Objective v1 for session-based continuation entries."""

    name: str = 'session_continuation_fvg'
    execution_timeframe: str = 'M3'
    london_start_hour_utc: int = 7
    london_end_hour_utc: int = 16   # Extended: was 11
    new_york_start_hour_utc: int = 12
    new_york_end_hour_utc: int = 21  # Extended: was 16
    displacement_atr_multiple: float = 1.4
    min_fvg_pips: float = 1.0
    entry_expiry_bars: int = 8
    max_bars_hold: int = 24
    rr_target: float = 2.0
    pip_size: float = 0.0001
    spread_points_per_pip: float = 10.0
    max_spread_pips: float = 1.5
    swing_lookback: int = 3
    stop_buffer_pips: float = 0.5
    impulse_lookback_bars: int = 8

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        result = add_session_columns(result, timezone_offset_hours=0)
        result = add_atr_column(result, period=14, column_name='atr14')
        result['date_utc'] = result.index.date
        result['is_london_window'] = result['hour_utc'].between(self.london_start_hour_utc, self.london_end_hour_utc - 1).astype(int)
        result['is_new_york_window'] = result['hour_utc'].between(self.new_york_start_hour_utc, self.new_york_end_hour_utc - 1).astype(int)
        result['is_trade_window'] = ((result['is_london_window'] == 1) | (result['is_new_york_window'] == 1)).astype(int)
        result['body_size'] = (result['close'] - result['open']).abs()
        result['body_atr_ratio'] = result['body_size'] / result['atr14'].clip(lower=self.pip_size)
        result['bullish_displacement'] = ((result['close'] > result['open']) & (result['body_atr_ratio'] >= self.displacement_atr_multiple)).astype(int)
        result['bearish_displacement'] = ((result['close'] < result['open']) & (result['body_atr_ratio'] >= self.displacement_atr_multiple)).astype(int)
        if 'spread' in result.columns:
            result['spread_pips'] = result['spread'] / self.spread_points_per_pip
        else:
            result['spread_pips'] = pd.NA
        # Backward-confirmed swing pivots: only use PAST data (no look-ahead)
        # A bar is a swing high if its high >= all highs in the previous `swing_lookback` bars
        rolling_high_max = result['high'].rolling(window=self.swing_lookback, min_periods=self.swing_lookback).max().shift(1)
        result['swing_high'] = (result['high'] >= rolling_high_max).fillna(False)

        rolling_low_min = result['low'].rolling(window=self.swing_lookback, min_periods=self.swing_lookback).min().shift(1)
        result['swing_low'] = (result['low'] <= rolling_low_min).fillna(False)
        return result

    def _spread_ok(self, row: pd.Series) -> bool:
        spread = row.get('spread_pips')
        if spread is None or pd.isna(spread):
            return True
        return float(spread) <= self.max_spread_pips

    def _recent_structure_level(self, df: pd.DataFrame, impulse_index: int, side: str) -> float | None:
        start = max(0, impulse_index - self.impulse_lookback_bars)
        subset = df.iloc[start:impulse_index]
        if subset.empty:
            return None
        if side == 'LONG':
            pivots = subset.loc[subset['swing_high'] == True, 'high']
            if pivots.empty:
                return None
            return float(pivots.iloc[-1])
        pivots = subset.loc[subset['swing_low'] == True, 'low']
        if pivots.empty:
            return None
        return float(pivots.iloc[-1])

    def _find_impulse_index(self, df: pd.DataFrame, index: int, side: str) -> int | None:
        start = max(1, index - self.impulse_lookback_bars)
        for candidate in range(index - 1, start - 1, -1):
            row = df.iloc[candidate]
            if int(row.get('is_trade_window', 0)) != 1:
                continue
            level = self._recent_structure_level(df, candidate, side)
            if level is None:
                continue
            if side == 'LONG' and int(row.get('bullish_displacement', 0)) == 1 and float(row['close']) > level:
                return candidate
            if side == 'SHORT' and int(row.get('bearish_displacement', 0)) == 1 and float(row['close']) < level:
                return candidate
        return None

    def _find_fvg_zone(self, df: pd.DataFrame, impulse_index: int, side: str) -> tuple[float, float] | None:
        start = max(2, impulse_index - 2)
        for candidate in range(impulse_index, start - 1, -1):
            a = df.iloc[candidate - 2]
            c = df.iloc[candidate]
            if side == 'LONG':
                gap_low = float(a['high'])
                gap_high = float(c['low'])
                if gap_high <= gap_low:
                    continue
            else:
                gap_low = float(c['high'])
                gap_high = float(a['low'])
                if gap_high <= gap_low:
                    continue
            gap_pips = (gap_high - gap_low) / self.pip_size
            if gap_pips >= self.min_fvg_pips:
                return gap_low, gap_high
        return None

    def _bar_touches_zone(self, row: pd.Series, zone: tuple[float, float]) -> bool:
        zone_low, zone_high = zone
        low = float(row['low'])
        high = float(row['high'])
        return low <= zone_high and high >= zone_low

    def _entry_from_retrace(self, df: pd.DataFrame, index: int, impulse_index: int, side: str, zone: tuple[float, float]) -> float | None:
        row = df.iloc[index]
        if not self._bar_touches_zone(row, zone):
            return None
        for prior_index in range(impulse_index + 1, index):
            if self._bar_touches_zone(df.iloc[prior_index], zone):
                return None
        zone_low, zone_high = zone
        close = float(row['close'])
        if side == 'LONG':
            return max(zone_low, min(close, zone_high))
        return min(zone_high, max(close, zone_low))

    def _build_signal(self, df: pd.DataFrame, index: int, side: str, impulse_index: int, zone: tuple[float, float], entry_price: float) -> TradeSignal | None:
        impulse_row = df.iloc[impulse_index]
        risk_anchor = float(impulse_row['low']) if side == 'LONG' else float(impulse_row['high'])
        stop_loss = risk_anchor - self.stop_buffer_pips * self.pip_size if side == 'LONG' else risk_anchor + self.stop_buffer_pips * self.pip_size
        if side == 'LONG' and stop_loss >= entry_price:
            return None
        if side == 'SHORT' and stop_loss <= entry_price:
            return None
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance < self.pip_size:
            return None
        take_profit = entry_price + self.rr_target * risk_distance if side == 'LONG' else entry_price - self.rr_target * risk_distance
        if side == 'LONG' and take_profit <= entry_price:
            return None
        if side == 'SHORT' and take_profit >= entry_price:
            return None
        zone_low, zone_high = zone
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
                'impulse_index': impulse_index,
                'fvg_low': zone_low,
                'fvg_high': zone_high,
                'rr_target': self.rr_target,
                'pip_size': self.pip_size,
                'spread_points_per_pip': self.spread_points_per_pip,
            },
        )

    def generate_signal(self, df: pd.DataFrame, index: int) -> TradeSignal | None:
        if index < max(self.swing_lookback * 2 + 3, 20):
            return None
        row = df.iloc[index]
        if int(row.get('is_trade_window', 0)) != 1:
            return None
        if not self._spread_ok(row):
            return None
        for side in ('LONG', 'SHORT'):
            impulse_index = self._find_impulse_index(df, index, side)
            if impulse_index is None:
                continue
            if index <= impulse_index or index - impulse_index > self.entry_expiry_bars:
                continue
            zone = self._find_fvg_zone(df, impulse_index, side)
            if zone is None:
                continue
            entry_price = self._entry_from_retrace(df, index, impulse_index, side, zone)
            if entry_price is None:
                continue
            signal = self._build_signal(df, index, side, impulse_index, zone, entry_price)
            if signal is not None:
                return signal
        return None

    def debug_signal(self, df: pd.DataFrame, index: int) -> dict:
        min_index = max(self.swing_lookback * 2 + 3, 20)
        if index < min_index:
            return self._debug_payload(False, 'WARMUP_NOT_REACHED', 'Not enough bars to evaluate continuation setup', details={'index': index, 'min_index': min_index})

        row = df.iloc[index]
        if int(row.get('is_trade_window', 0)) != 1:
            return self._debug_payload(
                False,
                'OUTSIDE_TRADE_WINDOW',
                'Current bar is outside London/New York trade window',
                details={'hour_utc': int(row.get('hour_utc', -1))},
            )
        if not self._spread_ok(row):
            spread = row.get('spread_pips')
            return self._debug_payload(False, 'SPREAD_TOO_WIDE', 'Spread is above the allowed threshold', details={'spread_pips': None if pd.isna(spread) else float(spread), 'max_spread_pips': self.max_spread_pips})

        side_debugs: list[dict] = []
        for side in ('LONG', 'SHORT'):
            impulse_index = self._find_impulse_index(df, index, side)
            if impulse_index is None:
                side_debugs.append(self._debug_payload(False, 'NO_IMPULSE_BREAK', 'No qualifying displacement break found recently', side=side))
                continue
            if index <= impulse_index:
                side_debugs.append(self._debug_payload(False, 'IMPULSE_NOT_CONFIRMED', 'Current bar is not after the impulse bar yet', side=side, details={'impulse_index': impulse_index}))
                continue
            if index - impulse_index > self.entry_expiry_bars:
                side_debugs.append(self._debug_payload(False, 'ENTRY_EXPIRED', 'Impulse is too old for a valid first retrace entry', side=side, details={'impulse_index': impulse_index, 'bars_since_impulse': index - impulse_index, 'entry_expiry_bars': self.entry_expiry_bars}))
                continue
            zone = self._find_fvg_zone(df, impulse_index, side)
            if zone is None:
                side_debugs.append(self._debug_payload(False, 'NO_FVG', 'Impulse exists but no valid fair value gap was found', side=side, details={'impulse_index': impulse_index}))
                continue
            entry_price = self._entry_from_retrace(df, index, impulse_index, side, zone)
            if entry_price is None:
                side_debugs.append(self._debug_payload(False, 'NO_FIRST_RETRACE', 'Price has not provided the first valid retrace into the FVG yet', side=side, details={'impulse_index': impulse_index, 'fvg_low': zone[0], 'fvg_high': zone[1]}))
                continue
            signal = self._build_signal(df, index, side, impulse_index, zone, entry_price)
            if signal is not None:
                return self._debug_signal_ready(signal)
            side_debugs.append(self._debug_payload(False, 'INVALID_RISK_GEOMETRY', 'Setup exists but stop-loss / take-profit geometry is invalid', side=side, details={'impulse_index': impulse_index, 'entry_price': entry_price, 'fvg_low': zone[0], 'fvg_high': zone[1]}))

        return self._combine_side_debugs(side_debugs)
