from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from xauusd_trading.data.mt5 import MT5Config, initialize_mt5, shutdown_mt5
from xauusd_trading.models.live import BrokerPosition, ExecutionDecision, OrderIntent, PositionManagementPlan, PositionSyncPlan
from xauusd_trading.models.trading import TradeSignal
from xauusd_trading.risk.manager import RiskConfig, RiskManager

logger = logging.getLogger(__name__)

# MT5 return codes that indicate successful order execution
_MT5_SUCCESS_RETCODES = frozenset({10008, 10009})


@dataclass(slots=True)
class MT5ExecutionConfig:
    symbol: str = 'XAUUSD'
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    deviation: int = 20
    magic: int = 35042026
    comment: str = 'xauusd_trading_bot'
    allow_live_send: bool = False
    close_opposite_first: bool = True
    sltp_tolerance_points: float = 0.05


class MT5ExecutionAdapter:
    def __init__(self, *, mt5_config: MT5Config, execution_config: MT5ExecutionConfig) -> None:
        self.mt5_config = mt5_config
        self.execution_config = execution_config

    def fetch_positions(self) -> list[BrokerPosition]:
        mt5 = initialize_mt5(self.mt5_config)
        try:
            raw_positions = mt5.positions_get(symbol=self.mt5_config.symbol)
            if raw_positions is None:
                return []
            positions: list[BrokerPosition] = []
            for position in raw_positions:
                side = 'LONG' if int(position.type) == getattr(mt5, 'POSITION_TYPE_BUY', 0) else 'SHORT'
                positions.append(
                    BrokerPosition(
                        ticket=int(position.ticket),
                        symbol=str(position.symbol),
                        side=side,
                        volume=float(position.volume),
                        price_open=float(position.price_open),
                        stop_loss=float(position.sl or 0.0),
                        take_profit=float(position.tp or 0.0),
                        price_current=float(position.price_current),
                        profit=float(position.profit),
                        magic=int(position.magic) if getattr(position, 'magic', None) is not None else None,
                        comment=str(position.comment) if getattr(position, 'comment', None) is not None else None,
                    )
                )
            return positions
        finally:
            shutdown_mt5(mt5)

    def build_intent(self, signal: TradeSignal, *, account_balance: float, risk_config: RiskConfig) -> OrderIntent:
        # BUG 3 fix: respect per-branch risk_per_trade from signal metadata
        risk_per_trade_override = signal.metadata.get('risk_per_trade')
        if isinstance(risk_per_trade_override, (int, float)) and float(risk_per_trade_override) > 0:
            effective_risk_config = RiskConfig(
                risk_per_trade=float(risk_per_trade_override),
                max_drawdown_pct=risk_config.max_drawdown_pct,
                max_consecutive_losses=risk_config.max_consecutive_losses,
                min_balance=risk_config.min_balance,
                max_position_lots=risk_config.max_position_lots,
                lot_size=risk_config.lot_size,
                min_risk_distance_pips=risk_config.min_risk_distance_pips,
            )
        else:
            effective_risk_config = risk_config

        risk_manager = RiskManager(initial_balance=account_balance, config=effective_risk_config)
        pip_size = float(signal.metadata.get('pip_size', 0.0001))
        symbol_hint = str(signal.metadata.get('symbol') or self.mt5_config.symbol).upper()
        default_lot_size = 100.0 if 'XAU' in symbol_hint else 100_000.0
        lot_size = float(signal.metadata.get('lot_size', default_lot_size))
        risk_budget = account_balance * effective_risk_config.risk_per_trade
        _, raw_units = risk_manager.size_position(
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            pip_size=pip_size,
            lot_size=lot_size,
        )
        # size_position() returns CONTRACT UNITS (e.g. 4,100), not lots.
        # Must convert to lots: lots = units / lot_size
        raw_lots = raw_units / lot_size
        volume = self._normalize_volume(raw_lots)
        return OrderIntent(
            symbol=self.mt5_config.symbol,
            side=signal.side,
            volume=volume,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            deviation=self.execution_config.deviation,
            magic=self.execution_config.magic,
            comment=self.execution_config.comment,
            metadata={
                **dict(signal.metadata),
                'lot_size': lot_size,
                'risk_budget': risk_budget,
                'effective_risk_per_trade': effective_risk_config.risk_per_trade,
                'estimated_risk_currency': volume * lot_size * abs(signal.entry_price - signal.stop_loss),
            },
        )

    def decide(self, *, signal: TradeSignal | None, broker_positions: list[BrokerPosition], account_balance: float, risk_config: RiskConfig) -> ExecutionDecision:
        same_side_positions = [position for position in broker_positions if signal is not None and position.side == signal.side]
        opposite_positions = [position for position in broker_positions if signal is not None and position.side != signal.side]

        if signal is None:
            return ExecutionDecision(action='HOLD', reason='NO_SIGNAL', broker_positions=broker_positions)

        if same_side_positions:
            management_plan = self.build_management_plan(signal, same_side_positions[0])
            if management_plan is not None:
                return ExecutionDecision(
                    action='MANAGE_POSITION',
                    reason='SAME_SIDE_POSITION_NEEDS_MANAGEMENT',
                    broker_positions=broker_positions,
                    management_plan=management_plan,
                    sync_plan=management_plan.sync_plan,
                )
            sync_plan = self.build_sync_plan(signal, same_side_positions[0])
            if sync_plan is not None:
                return ExecutionDecision(
                    action='SYNC_SLTP',
                    reason='SAME_SIDE_POSITION_NEEDS_SYNC',
                    broker_positions=broker_positions,
                    sync_plan=sync_plan,
                )
            return ExecutionDecision(action='HOLD', reason='SAME_SIDE_POSITION_ALREADY_SYNCED', broker_positions=broker_positions)

        intent = self.build_intent(signal, account_balance=account_balance, risk_config=risk_config)
        if intent.volume <= 0:
            return ExecutionDecision(action='HOLD', reason='INVALID_VOLUME', broker_positions=broker_positions, intent=intent)

        if opposite_positions and self.execution_config.close_opposite_first:
            return ExecutionDecision(
                action='REVERSE',
                reason='OPPOSITE_POSITION_EXISTS',
                broker_positions=broker_positions,
                intent=intent,
                metadata={'close_tickets_first': [position.ticket for position in opposite_positions]},
            )

        return ExecutionDecision(action='OPEN', reason='SIGNAL_READY', broker_positions=broker_positions, intent=intent)

    def send_market_order(self, intent: OrderIntent) -> dict[str, Any]:
        if not self.execution_config.allow_live_send:
            return {'sent': False, 'mode': 'DRY_RUN', 'intent': intent.to_dict()}

        mt5 = initialize_mt5(self.mt5_config)
        try:
            symbol_info = mt5.symbol_info(intent.symbol)
            if symbol_info is None:
                raise RuntimeError(f'Symbol not found in MT5: {intent.symbol}')
            if not symbol_info.visible:
                mt5.symbol_select(intent.symbol, True)

            tick = mt5.symbol_info_tick(intent.symbol)
            if tick is None:
                raise RuntimeError(f'No tick available for symbol: {intent.symbol}')

            order_type = mt5.ORDER_TYPE_BUY if intent.side == 'LONG' else mt5.ORDER_TYPE_SELL
            bid = float(tick.bid)
            ask = float(tick.ask)
            actual_price = ask if intent.side == 'LONG' else bid
            trigger_price = bid if intent.side == 'LONG' else ask
            spread_price = max(ask - bid, 0.0)

            # ── CRITICAL: preserve structural SL, do NOT tighten it toward price ──
            # Previous version shifted SL/TP by (actual_price - signal_entry). For XAUUSD
            # that can move SL closer to the bid/ask trigger side and cause immediate SL.
            # Safer live behavior:
            #   - keep structural SL from the strategy;
            #   - if broker min stop requires it, move SL only FARTHER away;
            #   - recompute TP from actual entry + actual risk * RR;
            #   - resize volume for the actual risk distance.
            signal_entry = intent.entry_price
            original_sl = intent.stop_loss
            original_tp = intent.take_profit
            original_risk_distance = abs(signal_entry - original_sl)
            original_reward_distance = abs(original_tp - signal_entry)
            rr_target = original_reward_distance / original_risk_distance if original_risk_distance > 0 else 2.0

            sl = original_sl

            # ── Broker minimum stops level check ──
            # MT5 stop levels are checked from the trigger side, not entry side:
            #   BUY position SL/TP trigger on BID
            #   SELL position SL/TP trigger on ASK
            point = float(getattr(symbol_info, 'point', 0.00001))
            stops_level = int(getattr(symbol_info, 'trade_stops_level', 0) or 0)
            freeze_level = int(getattr(symbol_info, 'trade_freeze_level', 0) or 0)
            spread_points = int(getattr(symbol_info, 'spread', 0) or 0)
            min_stop_points = max(stops_level, freeze_level, spread_points) * point
            # Use a larger buffer than 1 point so fast-moving XAUUSD doesn't instantly tag.
            min_stop_distance = min_stop_points + max(point * 5, spread_price)

            # Enforce SL distance from BID/ASK trigger side. Only widen risk.
            sl_adjusted = False
            if intent.side == 'LONG':
                max_allowed_sl = trigger_price - min_stop_distance
                if sl >= max_allowed_sl:
                    sl = max_allowed_sl
                    sl_adjusted = True
                    logger.warning(
                        'LONG SL too close to BID trigger. Adjusted SL farther: %.5f → %.5f (bid=%.5f min=%.5f)',
                        original_sl, sl, trigger_price, min_stop_distance,
                    )
            else:
                min_allowed_sl = trigger_price + min_stop_distance
                if sl <= min_allowed_sl:
                    sl = min_allowed_sl
                    sl_adjusted = True
                    logger.warning(
                        'SHORT SL too close to ASK trigger. Adjusted SL farther: %.5f → %.5f (ask=%.5f min=%.5f)',
                        original_sl, sl, trigger_price, min_stop_distance,
                    )

            actual_risk_distance = abs(actual_price - sl)
            if actual_risk_distance <= 0:
                return {
                    'sent': False,
                    'mode': 'LIVE',
                    'retcode': -1,
                    'reason': f'INVALID_ACTUAL_RISK_DISTANCE: price={actual_price} sl={sl}',
                    'price': actual_price,
                    'bid': bid,
                    'ask': ask,
                    'spread_price': spread_price,
                }

            # Recompute TP from actual entry and actual risk, then enforce trigger-side stops.
            if intent.side == 'LONG':
                tp = actual_price + rr_target * actual_risk_distance
                min_allowed_tp = trigger_price + min_stop_distance
                if tp <= min_allowed_tp:
                    tp = min_allowed_tp
            else:
                tp = actual_price - rr_target * actual_risk_distance
                max_allowed_tp = trigger_price - min_stop_distance
                if tp >= max_allowed_tp:
                    tp = max_allowed_tp

            # Re-validate against trigger side and actual entry.
            if intent.side == 'LONG':
                if not (sl < trigger_price and actual_price < tp):
                    logger.error(
                        'LONG SL/TP invalid: bid=%.5f ask=%.5f entry=%.5f SL=%.5f TP=%.5f',
                        bid, ask, actual_price, sl, tp,
                    )
                    return {
                        'sent': False, 'mode': 'LIVE', 'retcode': -1,
                        'reason': f'SL_TP_INVALID_LONG: bid={bid} ask={ask} entry={actual_price} sl={sl} tp={tp}',
                        'price': actual_price, 'bid': bid, 'ask': ask, 'spread_price': spread_price,
                    }
            else:
                if not (tp < actual_price and trigger_price < sl):
                    logger.error(
                        'SHORT SL/TP invalid: bid=%.5f ask=%.5f entry=%.5f SL=%.5f TP=%.5f',
                        bid, ask, actual_price, sl, tp,
                    )
                    return {
                        'sent': False, 'mode': 'LIVE', 'retcode': -1,
                        'reason': f'SL_TP_INVALID_SHORT: bid={bid} ask={ask} entry={actual_price} sl={sl} tp={tp}',
                        'price': actual_price, 'bid': bid, 'ask': ask, 'spread_price': spread_price,
                    }

            # ── Re-size position based on actual risk distance ──
            # Wider actual risk → lower lots. Never clip upward to min_lot silently if risk
            # would exceed budget; reject instead.
            volume_ratio = original_risk_distance / actual_risk_distance if actual_risk_distance > 0 else 0.0
            raw_adjusted_volume = intent.volume * volume_ratio
            if raw_adjusted_volume < self.execution_config.min_lot:
                return {
                    'sent': False,
                    'mode': 'LIVE',
                    'retcode': -1,
                    'reason': f'ADJUSTED_VOLUME_BELOW_MIN_LOT: raw={raw_adjusted_volume:.4f} min={self.execution_config.min_lot}',
                    'price': actual_price,
                    'bid': bid,
                    'ask': ask,
                    'spread_price': spread_price,
                    'sl_submitted': sl,
                    'tp_submitted': tp,
                }
            adjusted_volume = self._normalize_volume(raw_adjusted_volume)
            lot_size = float(intent.metadata.get('lot_size', 100.0 if 'XAU' in intent.symbol.upper() else 100_000.0))
            risk_budget = float(intent.metadata.get('risk_budget', 0.0) or 0.0)
            estimated_actual_risk = adjusted_volume * lot_size * actual_risk_distance
            # Final live safety guard: if the order would risk materially more than
            # the configured budget, reject instead of sending an oversized trade.
            if risk_budget > 0 and estimated_actual_risk > risk_budget * 1.10:
                return {
                    'sent': False,
                    'mode': 'LIVE',
                    'retcode': -1,
                    'reason': (
                        'RISK_BUDGET_EXCEEDED: '
                        f'estimated={estimated_actual_risk:.2f} budget={risk_budget:.2f} '
                        f'volume={adjusted_volume:.2f} lot_size={lot_size:.0f}'
                    ),
                    'price': actual_price,
                    'bid': bid,
                    'ask': ask,
                    'spread_price': spread_price,
                    'volume_submitted': adjusted_volume,
                    'sl_submitted': sl,
                    'tp_submitted': tp,
                    'actual_risk_distance': actual_risk_distance,
                    'risk_budget': risk_budget,
                    'estimated_actual_risk': estimated_actual_risk,
                }
            if abs(adjusted_volume - intent.volume) > self.execution_config.lot_step:
                logger.info(
                    'Volume adjusted: %.2f → %.2f (risk dist %.5f → %.5f)',
                    intent.volume, adjusted_volume, original_risk_distance, actual_risk_distance,
                )
            intent = OrderIntent(
                symbol=intent.symbol,
                side=intent.side,
                volume=adjusted_volume,
                entry_price=actual_price,
                stop_loss=sl,
                take_profit=tp,
                deviation=intent.deviation,
                magic=intent.magic,
                comment=intent.comment,
                metadata={
                    **intent.metadata,
                    'original_volume': intent.volume,
                    'sl_adjusted': sl_adjusted,
                    'original_signal_entry': signal_entry,
                    'original_stop_loss': original_sl,
                    'original_take_profit': original_tp,
                    'actual_bid': bid,
                    'actual_ask': ask,
                    'spread_price': spread_price,
                    'rr_target_live': rr_target,
                },
            )

            request = {
                'action': mt5.TRADE_ACTION_DEAL,
                'symbol': intent.symbol,
                'volume': intent.volume,
                'type': order_type,
                'price': actual_price,
                'sl': sl,
                'tp': tp,
                'deviation': intent.deviation,
                'magic': intent.magic,
                'comment': intent.comment,
                'type_time': mt5.ORDER_TIME_GTC,
                'type_filling': getattr(mt5, 'ORDER_FILLING_FOK', 0),
            }
            result = mt5.order_send(request)
            if result is None:
                raise RuntimeError(f'MT5 order_send returned None: {mt5.last_error()}')
            retcode = int(getattr(result, 'retcode', -1))
            sent = retcode in _MT5_SUCCESS_RETCODES
            if not sent:
                logger.warning('Order rejected by broker: retcode=%d comment=%s', retcode, getattr(result, 'comment', ''))
            return {
                'sent': sent,
                'mode': 'LIVE',
                'retcode': retcode,
                'order': int(getattr(result, 'order', 0)),
                'deal': int(getattr(result, 'deal', 0)),
                'price': actual_price,
                'bid': bid,
                'ask': ask,
                'trigger_price': trigger_price,
                'spread_price': spread_price,
                'volume_submitted': intent.volume,
                'sl_submitted': sl,
                'tp_submitted': tp,
                'original_signal_entry': signal_entry,
                'original_stop_loss': original_sl,
                'original_take_profit': original_tp,
                'actual_risk_distance': actual_risk_distance,
                'risk_budget': risk_budget,
                'estimated_actual_risk': estimated_actual_risk,
                'lot_size': lot_size,
                'rr_target_live': rr_target,
                'stops_level_points': stops_level,
                'freeze_level_points': freeze_level,
                'spread_points': spread_points,
                'min_stop_distance': min_stop_distance,
                'request': request,
            }
        finally:
            shutdown_mt5(mt5)

    def build_management_plan(self, signal: TradeSignal, position: BrokerPosition) -> PositionManagementPlan | None:
        stop_distance = abs(position.price_open - signal.stop_loss)
        if stop_distance <= 0:
            return None

        rr_multiple = float(signal.metadata.get('partial_tp_rr', 0.0) or 0.0)
        partial_fraction = float(signal.metadata.get('partial_close_fraction', 0.0) or 0.0)
        move_be = bool(signal.metadata.get('move_stop_to_breakeven_on_partial', True))
        trail_atr_multiple = signal.metadata.get('trail_atr_multiple')
        atr = float(signal.metadata.get('atr14', 0.0) or 0.0)
        tolerance = max(self.execution_config.sltp_tolerance_points, 0.0)

        partial_close_volume = 0.0
        partial_close_reason = None
        target_sl = position.stop_loss
        target_tp = position.take_profit
        metadata: dict[str, Any] = {'strategy': signal.metadata.get('strategy')}

        if rr_multiple > 0 and partial_fraction > 0:
            if signal.side == 'LONG':
                partial_price = position.price_open + stop_distance * rr_multiple
                partial_hit = position.price_current >= partial_price
                be_target = max(position.stop_loss, position.price_open) if move_be else position.stop_loss
            else:
                partial_price = position.price_open - stop_distance * rr_multiple
                partial_hit = position.price_current <= partial_price
                be_target = min(position.stop_loss, position.price_open) if move_be else position.stop_loss

            metadata['partial_target_price'] = partial_price
            if partial_hit and position.volume >= max(self.execution_config.min_lot * 2, self.execution_config.min_lot + self.execution_config.lot_step):
                partial_close_volume = self._normalize_volume(position.volume * partial_fraction)
                if partial_close_volume >= self.execution_config.min_lot and partial_close_volume < position.volume:
                    partial_close_reason = 'PARTIAL_TP_THRESHOLD_HIT'
                if move_be:
                    target_sl = be_target

        tp_half_triggered = False
        if trail_atr_multiple is not None and atr > 0:
            if signal.side == 'LONG':
                tp_half = position.price_open + (signal.take_profit - position.price_open) * 0.5
                tp_half_triggered = position.price_current >= tp_half
                if tp_half_triggered:
                    target_sl = max(target_sl, position.price_current - atr * float(trail_atr_multiple))
            else:
                tp_half = position.price_open - (position.price_open - signal.take_profit) * 0.5
                tp_half_triggered = position.price_current <= tp_half
                if tp_half_triggered:
                    target_sl = min(target_sl, position.price_current + atr * float(trail_atr_multiple))
            metadata['tp_half_price'] = tp_half

        sync_plan = None
        sl_diff = abs(position.stop_loss - target_sl)
        tp_diff = abs(position.take_profit - target_tp)
        if sl_diff > tolerance or tp_diff > tolerance:
            sync_plan = PositionSyncPlan(
                ticket=position.ticket,
                symbol=position.symbol,
                target_stop_loss=target_sl,
                target_take_profit=target_tp,
                current_stop_loss=position.stop_loss,
                current_take_profit=position.take_profit,
                side=position.side,
                reason='POSITION_MANAGEMENT_UPDATE',
                metadata={
                    **metadata,
                    'sl_diff': sl_diff,
                    'tp_diff': tp_diff,
                    'tp_half_triggered': tp_half_triggered,
                },
            )

        if partial_close_volume <= 0 and sync_plan is None:
            return None

        return PositionManagementPlan(
            ticket=position.ticket,
            symbol=position.symbol,
            side=position.side,
            current_volume=position.volume,
            partial_close_volume=partial_close_volume,
            partial_close_reason=partial_close_reason,
            sync_plan=sync_plan,
            metadata=metadata,
        )

    def build_sync_plan(self, signal: TradeSignal, position: BrokerPosition) -> PositionSyncPlan | None:
        sl_diff = abs(position.stop_loss - signal.stop_loss)
        tp_diff = abs(position.take_profit - signal.take_profit)
        tolerance = max(self.execution_config.sltp_tolerance_points, 0.0)
        if sl_diff <= tolerance and tp_diff <= tolerance:
            return None
        return PositionSyncPlan(
            ticket=position.ticket,
            symbol=position.symbol,
            target_stop_loss=signal.stop_loss,
            target_take_profit=signal.take_profit,
            current_stop_loss=position.stop_loss,
            current_take_profit=position.take_profit,
            side=position.side,
            reason='SIGNAL_LEVELS_CHANGED',
            metadata={
                'strategy': signal.metadata.get('strategy'),
                'sl_diff': sl_diff,
                'tp_diff': tp_diff,
            },
        )

    def modify_position_sltp(self, sync_plan: PositionSyncPlan) -> dict[str, Any]:
        if not self.execution_config.allow_live_send:
            return {'sent': False, 'mode': 'DRY_RUN', 'sync_plan': sync_plan.to_dict()}

        mt5 = initialize_mt5(self.mt5_config)
        try:
            request = {
                'action': mt5.TRADE_ACTION_SLTP,
                'symbol': sync_plan.symbol,
                'position': sync_plan.ticket,
                'sl': sync_plan.target_stop_loss,
                'tp': sync_plan.target_take_profit,
                'magic': self.execution_config.magic,
                'comment': f'{self.execution_config.comment}_sync',
            }
            result = mt5.order_send(request)
            if result is None:
                raise RuntimeError(f'MT5 order_send SLTP returned None: {mt5.last_error()}')
            retcode = int(getattr(result, 'retcode', -1))
            sent = retcode in _MT5_SUCCESS_RETCODES
            if not sent:
                logger.warning('SLTP modification rejected: retcode=%d comment=%s', retcode, getattr(result, 'comment', ''))
            return {
                'sent': sent,
                'mode': 'LIVE',
                'retcode': retcode,
                'order': int(getattr(result, 'order', 0)),
                'request': request,
            }
        finally:
            shutdown_mt5(mt5)

    def partial_close_position(self, *, ticket: int, volume: float) -> dict[str, Any]:
        volume = self._normalize_volume(volume)
        if volume < self.execution_config.min_lot:
            return {'sent': False, 'mode': 'DRY_RUN' if not self.execution_config.allow_live_send else 'LIVE', 'ticket': ticket, 'reason': 'VOLUME_TOO_SMALL'}
        if not self.execution_config.allow_live_send:
            return {'sent': False, 'mode': 'DRY_RUN', 'ticket': ticket, 'volume': volume}

        mt5 = initialize_mt5(self.mt5_config)
        try:
            positions = mt5.positions_get(symbol=self.mt5_config.symbol) or []
            position = next((item for item in positions if int(item.ticket) == ticket), None)
            if position is None:
                return {'sent': False, 'ticket': ticket, 'reason': 'POSITION_NOT_FOUND'}
            tick = mt5.symbol_info_tick(position.symbol)
            if tick is None:
                return {'sent': False, 'ticket': ticket, 'reason': 'NO_TICK'}
            is_buy = int(position.type) == getattr(mt5, 'POSITION_TYPE_BUY', 0)
            request = {
                'action': mt5.TRADE_ACTION_DEAL,
                'symbol': position.symbol,
                'position': int(position.ticket),
                'volume': volume,
                'type': mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                'price': float(tick.bid if is_buy else tick.ask),
                'deviation': self.execution_config.deviation,
                'magic': self.execution_config.magic,
                'comment': f'{self.execution_config.comment}_partial',
                'type_time': mt5.ORDER_TIME_GTC,
                'type_filling': getattr(mt5, 'ORDER_FILLING_FOK', 0),
            }
            result = mt5.order_send(request)
            retcode = int(getattr(result, 'retcode', -1)) if result is not None else -1
            sent = result is not None and retcode in _MT5_SUCCESS_RETCODES
            if result is not None and not sent:
                logger.warning('Partial close rejected: retcode=%d ticket=%d comment=%s', retcode, ticket, getattr(result, 'comment', ''))
            return {
                'sent': sent,
                'mode': 'LIVE',
                'ticket': ticket,
                'retcode': retcode,
                'request': request,
            }
        finally:
            shutdown_mt5(mt5)

    def execute_management_plan(self, plan: PositionManagementPlan) -> dict[str, Any]:
        partial_result = None
        sync_result = None
        if plan.partial_close_volume > 0:
            partial_result = self.partial_close_position(ticket=plan.ticket, volume=plan.partial_close_volume)
        if plan.sync_plan is not None:
            sync_result = self.modify_position_sltp(plan.sync_plan)
        return {
            'ticket': plan.ticket,
            'partial_result': partial_result,
            'sync_result': sync_result,
            'plan': plan.to_dict(),
        }

    def close_positions(self, tickets: list[int]) -> list[dict[str, Any]]:
        if not tickets:
            return []
        if not self.execution_config.allow_live_send:
            return [{'sent': False, 'mode': 'DRY_RUN', 'ticket': ticket} for ticket in tickets]

        mt5 = initialize_mt5(self.mt5_config)
        try:
            current_positions = mt5.positions_get(symbol=self.mt5_config.symbol) or []
            results: list[dict[str, Any]] = []
            by_ticket = {int(position.ticket): position for position in current_positions}
            for ticket in tickets:
                position = by_ticket.get(ticket)
                if position is None:
                    results.append({'sent': False, 'ticket': ticket, 'reason': 'POSITION_NOT_FOUND'})
                    continue
                tick = mt5.symbol_info_tick(position.symbol)
                if tick is None:
                    results.append({'sent': False, 'ticket': ticket, 'reason': 'NO_TICK'})
                    continue
                is_buy = int(position.type) == getattr(mt5, 'POSITION_TYPE_BUY', 0)
                request = {
                    'action': mt5.TRADE_ACTION_DEAL,
                    'symbol': position.symbol,
                    'position': int(position.ticket),
                    'volume': float(position.volume),
                    'type': mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                    'price': float(tick.bid if is_buy else tick.ask),
                    'deviation': self.execution_config.deviation,
                    'magic': self.execution_config.magic,
                    'comment': f'{self.execution_config.comment}_close',
                    'type_time': mt5.ORDER_TIME_GTC,
                    'type_filling': getattr(mt5, 'ORDER_FILLING_FOK', 0),
                }
                result = mt5.order_send(request)
                retcode = int(getattr(result, 'retcode', -1)) if result is not None else -1
                sent = result is not None and retcode in _MT5_SUCCESS_RETCODES
                if result is not None and not sent:
                    logger.warning('Position close rejected: retcode=%d ticket=%d comment=%s', retcode, ticket, getattr(result, 'comment', ''))
                results.append({
                    'sent': sent,
                    'ticket': ticket,
                    'retcode': retcode,
                    'request': request,
                })
            return results
        finally:
            shutdown_mt5(mt5)

    def _normalize_volume(self, raw_volume: float) -> float:
        step = max(self.execution_config.lot_step, 0.0001)
        clipped = max(self.execution_config.min_lot, min(raw_volume, self.execution_config.max_lot))
        units = round(clipped / step)
        normalized = units * step
        return round(normalized, 4)
