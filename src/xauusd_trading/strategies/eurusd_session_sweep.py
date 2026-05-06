from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xauusd_trading.features.indicators import add_atr_column, add_session_columns
from xauusd_trading.models.trading import TradeSignal
from xauusd_trading.strategies.base import Strategy


@dataclass(slots=True)
class EURUSDSessionSweepFVGStrategy(Strategy):
    """Objective v1 for the EURUSD session sweep branch.

    Current assumptions:
    - single-timeframe execution dataset, even though the manual workflow uses M15 bias + M5/M3 execution
    - Asia range is built from UTC hours on the same dataframe
    - sweep, MSS, and FVG are derived mechanically with no discretionary overrides
    """

    name: str = 'eurusd_session_sweep_fvg'
    bias_timeframe: str = 'M15'
    execution_timeframe: str = 'M5'
    asia_start_hour_utc: int = 0
    asia_end_hour_utc: int = 8      # Extended: was 6 (Asia includes Tokyo+Sydney)
    london_start_hour_utc: int = 7
    london_end_hour_utc: int = 16   # Extended: was 11 (London active until close)
    new_york_start_hour_utc: int = 12
    new_york_end_hour_utc: int = 21  # Extended: was 16 (NY open to close 17:00 EDT)
    min_sweep_pips: float = 3.0
    displacement_atr_multiple: float = 1.4
    min_fvg_pips: float = 1.5
    enable_asia_sweep: bool = True
    enable_london_sweep: bool = False
    entry_expiry_bars: int = 6
    max_bars_hold: int = 24
    rr_target: float = 2.5
    max_spread_pips: float = 1.5
    spread_points_per_pip: float = 10.0
    pip_size: float = 0.0001
    swing_lookback: int = 3
    sweep_lookback_bars: int = 12
    min_asia_range_pips: float = 8.0
    max_asia_range_pips: float = 80.0
    stop_buffer_pips: float = 0.5

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        result = add_session_columns(result, timezone_offset_hours=0)
        result = add_atr_column(result, period=14, column_name='atr14')
        result['date_utc'] = result.index.date
        result['is_asia_session'] = result['hour_utc'].between(self.asia_start_hour_utc, self.asia_end_hour_utc - 1).astype(int)
        result['is_london_window'] = result['hour_utc'].between(self.london_start_hour_utc, self.london_end_hour_utc - 1).astype(int)
        result['is_new_york_window'] = result['hour_utc'].between(self.new_york_start_hour_utc, self.new_york_end_hour_utc - 1).astype(int)
        result['is_trade_window'] = ((result['is_london_window'] == 1) | (result['is_new_york_window'] == 1)).astype(int)

        asia_high = result['high'].where(result['is_asia_session'] == 1)
        asia_low = result['low'].where(result['is_asia_session'] == 1)
        result['asia_high'] = asia_high.groupby(result['date_utc']).cummax()
        result['asia_low'] = asia_low.groupby(result['date_utc']).cummin()
        result['asia_high'] = result.groupby('date_utc')['asia_high'].ffill()
        result['asia_low'] = result.groupby('date_utc')['asia_low'].ffill()
        result['asia_mid'] = (result['asia_high'] + result['asia_low']) / 2
        result['asia_range_pips'] = (result['asia_high'] - result['asia_low']) / self.pip_size

        london_high = result['high'].where(result['is_london_window'] == 1)
        london_low = result['low'].where(result['is_london_window'] == 1)
        result['london_high'] = london_high.groupby(result['date_utc']).cummax()
        result['london_low'] = london_low.groupby(result['date_utc']).cummin()
        result['london_high'] = result.groupby('date_utc')['london_high'].ffill()
        result['london_low'] = result.groupby('date_utc')['london_low'].ffill()
        result['london_mid'] = (result['london_high'] + result['london_low']) / 2
        result['london_range_pips'] = (result['london_high'] - result['london_low']) / self.pip_size

        result['sweep_high_pips'] = ((result['high'] - result['asia_high']) / self.pip_size).clip(lower=0.0)
        result['sweep_low_pips'] = ((result['asia_low'] - result['low']) / self.pip_size).clip(lower=0.0)
        result['swept_asia_high'] = (result['sweep_high_pips'] >= self.min_sweep_pips).astype(int)
        result['swept_asia_low'] = (result['sweep_low_pips'] >= self.min_sweep_pips).astype(int)
        result['london_sweep_high_pips'] = ((result['high'] - result['london_high']) / self.pip_size).clip(lower=0.0)
        result['london_sweep_low_pips'] = ((result['london_low'] - result['low']) / self.pip_size).clip(lower=0.0)
        result['swept_london_high'] = (result['london_sweep_high_pips'] >= self.min_sweep_pips).astype(int)
        result['swept_london_low'] = (result['london_sweep_low_pips'] >= self.min_sweep_pips).astype(int)
        if 'spread' in result.columns:
            result['spread_pips'] = result['spread'] / self.spread_points_per_pip
        else:
            result['spread_pips'] = pd.NA
        result['body_size'] = (result['close'] - result['open']).abs()
        result['body_atr_ratio'] = result['body_size'] / result['atr14'].clip(lower=self.pip_size)
        result['bearish_displacement'] = ((result['close'] < result['open']) & (result['body_atr_ratio'] >= self.displacement_atr_multiple)).astype(int)
        result['bullish_displacement'] = ((result['close'] > result['open']) & (result['body_atr_ratio'] >= self.displacement_atr_multiple)).astype(int)

        # Backward-confirmed swing pivots: a bar is a swing high if its high
        # is >= all highs in the previous `swing_lookback` bars (no future data).
        # This pivot is "confirmed" immediately (no future bars needed).
        rolling_high_max = result['high'].rolling(window=self.swing_lookback, min_periods=self.swing_lookback).max().shift(1)
        result['swing_high'] = result['high'] >= rolling_high_max

        rolling_low_min = result['low'].rolling(window=self.swing_lookback, min_periods=self.swing_lookback).min().shift(1)
        result['swing_low'] = result['low'] <= rolling_low_min
        return result

    def _spread_ok(self, row: pd.Series) -> bool:
        spread = row.get('spread_pips')
        if spread is None or pd.isna(spread):
            return True
        return float(spread) <= self.max_spread_pips

    def _asia_range_ok(self, row: pd.Series) -> bool:
        asia_range = float(row.get('asia_range_pips', 0.0) or 0.0)
        return self.min_asia_range_pips <= asia_range <= self.max_asia_range_pips

    def _sweep_triggers(self, row: pd.Series, side: str, current_row: pd.Series) -> list[tuple[str, float, float]]:
        triggers: list[tuple[str, float, float]] = []
        if self.enable_asia_sweep:
            if side == 'LONG' and int(row.get('swept_asia_low', 0)) == 1:
                triggers.append(('asia', float(row['asia_high']), float(row['asia_low'])))
            if side == 'SHORT' and int(row.get('swept_asia_high', 0)) == 1:
                triggers.append(('asia', float(row['asia_high']), float(row['asia_low'])))
        if self.enable_london_sweep and int(current_row.get('is_new_york_window', 0)) == 1:
            if side == 'LONG' and int(row.get('swept_london_low', 0)) == 1:
                triggers.append(('london', float(row['london_high']), float(row['london_low'])))
            if side == 'SHORT' and int(row.get('swept_london_high', 0)) == 1:
                triggers.append(('london', float(row['london_high']), float(row['london_low'])))
        return triggers

    def _recent_sweep_index(self, df: pd.DataFrame, index: int, side: str) -> tuple[int, str, tuple[float, float]] | None:
        start = max(0, index - self.sweep_lookback_bars)
        current_row = df.iloc[index]
        for candidate in range(index - 1, start - 1, -1):
            row = df.iloc[candidate]
            if int(row.get('is_trade_window', 0)) != 1:
                continue
            if not self._asia_range_ok(row):
                continue
            triggers = self._sweep_triggers(row, side, current_row)
            if triggers:
                trigger_name, range_high, range_low = triggers[0]
                return candidate, trigger_name, (range_high, range_low)
        return None

    def _recent_structure_level(self, df: pd.DataFrame, sweep_index: int, side: str) -> float | None:
        start = max(0, sweep_index - self.sweep_lookback_bars)
        subset = df.iloc[start:sweep_index]
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

    def _find_mss_index(self, df: pd.DataFrame, sweep_index: int, index: int, side: str) -> int | None:
        structure_level = self._recent_structure_level(df, sweep_index, side)
        if structure_level is None:
            return None
        for candidate in range(sweep_index + 1, index + 1):
            row = df.iloc[candidate]
            if side == 'LONG':
                if int(row.get('bullish_displacement', 0)) == 1 and float(row['close']) > structure_level:
                    return candidate
            else:
                if int(row.get('bearish_displacement', 0)) == 1 and float(row['close']) < structure_level:
                    return candidate
        return None

    fvg_search_radius: int = 6          # How far back from sweep to search for FVG (was implicit 2)

    def _find_fvg_zone(self, df: pd.DataFrame, mss_index: int, side: str) -> tuple[float, float] | None:
        start = max(2, mss_index - self.fvg_search_radius)
        for candidate in range(mss_index, start - 1, -1):
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

    def _entry_from_retrace(self, df: pd.DataFrame, index: int, mss_index: int, side: str, zone: tuple[float, float]) -> float | None:
        row = df.iloc[index]
        if not self._bar_touches_zone(row, zone):
            return None

        for prior_index in range(mss_index + 1, index):
            if self._bar_touches_zone(df.iloc[prior_index], zone):
                return None

        zone_low, zone_high = zone
        close = float(row['close'])
        if side == 'LONG':
            return max(zone_low, min(close, zone_high))
        return min(zone_high, max(close, zone_low))

    def _build_signal(self, df: pd.DataFrame, index: int, side: str, sweep_index: int, mss_index: int, zone: tuple[float, float], entry_price: float, sweep_source: str, trigger_range: tuple[float, float]) -> TradeSignal | None:
        sweep_row = df.iloc[sweep_index]
        row = df.iloc[index]
        sweep_extreme = float(sweep_row['low']) if side == 'LONG' else float(sweep_row['high'])
        stop_loss = sweep_extreme - self.stop_buffer_pips * self.pip_size if side == 'LONG' else sweep_extreme + self.stop_buffer_pips * self.pip_size
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance < self.pip_size:
            return None

        trigger_high, trigger_low = trigger_range
        asia_target = trigger_high if side == 'LONG' else trigger_low
        rr_target = entry_price + self.rr_target * risk_distance if side == 'LONG' else entry_price - self.rr_target * risk_distance
        take_profit = max(asia_target, rr_target) if side == 'LONG' else min(asia_target, rr_target)
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
                'bias_timeframe': self.bias_timeframe,
                'execution_timeframe': self.execution_timeframe,
                'sweep_index': sweep_index,
                'sweep_source': sweep_source,
                'mss_index': mss_index,
                'fvg_low': zone_low,
                'fvg_high': zone_high,
                'asia_high': float(row['asia_high']),
                'asia_low': float(row['asia_low']),
                'trigger_range_high': trigger_high,
                'trigger_range_low': trigger_low,
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
        if not self._asia_range_ok(row):
            return None

        for side in ('LONG', 'SHORT'):
            sweep_info = self._recent_sweep_index(df, index, side)
            if sweep_info is None:
                continue
            sweep_index, sweep_source, trigger_range = sweep_info
            mss_index = self._find_mss_index(df, sweep_index, index, side)
            if mss_index is None or index <= mss_index or index - mss_index > self.entry_expiry_bars:
                continue
            zone = self._find_fvg_zone(df, mss_index, side)
            if zone is None:
                continue
            entry_price = self._entry_from_retrace(df, index, mss_index, side, zone)
            if entry_price is None:
                continue
            signal = self._build_signal(df, index, side, sweep_index, mss_index, zone, entry_price, sweep_source, trigger_range)
            if signal is not None:
                return signal
        return None

    def debug_signal(self, df: pd.DataFrame, index: int) -> dict:
        min_index = max(self.swing_lookback * 2 + 3, 20)
        if index < min_index:
            return self._debug_payload(False, 'WARMUP_NOT_REACHED', 'Not enough bars to evaluate session sweep setup', details={'index': index, 'min_index': min_index})

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
        if not self._asia_range_ok(row):
            asia_range = row.get('asia_range_pips')
            return self._debug_payload(False, 'ASIA_RANGE_FILTER_BLOCKED', 'Asia range is outside the allowed bounds', details={'asia_range_pips': None if pd.isna(asia_range) else float(asia_range), 'min_asia_range_pips': self.min_asia_range_pips, 'max_asia_range_pips': self.max_asia_range_pips})

        side_debugs: list[dict] = []
        for side in ('LONG', 'SHORT'):
            sweep_info = self._recent_sweep_index(df, index, side)
            if sweep_info is None:
                side_debugs.append(self._debug_payload(False, 'NO_RECENT_SWEEP', 'No qualifying Asia/London sweep found recently', side=side))
                continue
            sweep_index, sweep_source, trigger_range = sweep_info
            mss_index = self._find_mss_index(df, sweep_index, index, side)
            if mss_index is None:
                side_debugs.append(self._debug_payload(False, 'NO_MSS', 'Sweep exists but there is no qualifying market-structure shift yet', side=side, details={'sweep_index': sweep_index, 'sweep_source': sweep_source}))
                continue
            if index <= mss_index:
                side_debugs.append(self._debug_payload(False, 'MSS_NOT_CONFIRMED', 'Current bar is not after the MSS bar yet', side=side, details={'mss_index': mss_index, 'sweep_index': sweep_index}))
                continue
            if index - mss_index > self.entry_expiry_bars:
                side_debugs.append(self._debug_payload(False, 'ENTRY_EXPIRED', 'MSS is too old for a valid first retrace entry', side=side, details={'mss_index': mss_index, 'bars_since_mss': index - mss_index, 'entry_expiry_bars': self.entry_expiry_bars}))
                continue
            zone = self._find_fvg_zone(df, mss_index, side)
            if zone is None:
                side_debugs.append(self._debug_payload(False, 'NO_FVG', 'MSS exists but no valid fair value gap was found', side=side, details={'mss_index': mss_index, 'sweep_index': sweep_index}))
                continue
            entry_price = self._entry_from_retrace(df, index, mss_index, side, zone)
            if entry_price is None:
                side_debugs.append(self._debug_payload(False, 'NO_FIRST_RETRACE', 'Price has not provided the first valid retrace into the FVG yet', side=side, details={'mss_index': mss_index, 'fvg_low': zone[0], 'fvg_high': zone[1]}))
                continue
            signal = self._build_signal(df, index, side, sweep_index, mss_index, zone, entry_price, sweep_source, trigger_range)
            if signal is not None:
                return self._debug_signal_ready(signal)
            side_debugs.append(self._debug_payload(False, 'INVALID_RISK_GEOMETRY', 'Setup exists but stop-loss / take-profit geometry is invalid', side=side, details={'sweep_index': sweep_index, 'mss_index': mss_index, 'entry_price': entry_price, 'fvg_low': zone[0], 'fvg_high': zone[1], 'trigger_range_high': trigger_range[0], 'trigger_range_low': trigger_range[1]}))

        return self._combine_side_debugs(side_debugs)
