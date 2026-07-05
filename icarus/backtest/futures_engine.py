"""icarus.backtest.futures_engine — Backtest engine for Futures markets.

Extends the spot BacktestEngine with:
  → Leverage-aware position sizing (risk-based notional capped by margin)
  → Margin tracking
  → Liquidation price monitoring
  → Partial close (TP1) support — keeps trade alive for TP2

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from icarus.config.models import ScalpingConfig
from icarus.core.types import (
    Candle,
    Direction,
    MarketSnapshot,
    MicroStructure,
    TradeAction,
)
from icarus.signal.engine import ScalpingSignalEngine

logger = logging.getLogger(__name__)

SIMULATED_SPREAD = 0.0002
SIMULATED_IMBALANCE = 0.5
MIN_WARMUP_CANDLES = 50


@dataclass
class FuturesBacktestResult:
    """Complete result of a futures backtest."""
    symbol: str
    config_summary: dict
    trades: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    skipped_signals: int = 0
    total_signals: int = 0
    elapsed_seconds: float = 0.0
    total_liquidations: int = 0


class FuturesBacktestEngine:
    """Backtest engine for futures scalping.

    Reuses ``ScalpingSignalEngine`` for signal generation but simulates
    futures-specific mechanics: leverage, margin, liquidation price,
    and partial closes at TP1.
    """

    def __init__(self, config: ScalpingConfig):
        self._config = config
        self._signal_engine = ScalpingSignalEngine(config)
        self._leverage = config.leverage
        self._margin_mode = config.margin_mode

    # ═════════════════════════════════════════════════════════════════════
    # Public API
    # ═════════════════════════════════════════════════════════════════════

    def run(
        self,
        csv_path: Path,
        symbol: str,
        initial_balance: float = 100.0,
        max_trades: Optional[int] = None,
    ) -> FuturesBacktestResult:
        t0 = time.time()

        candles = self._load_csv(csv_path)
        if len(candles) < MIN_WARMUP_CANDLES + 10:
            logger.error(
                f"Not enough candles ({len(candles)}) — need minimum {MIN_WARMUP_CANDLES + 10}"
            )
            return FuturesBacktestResult(
                symbol=symbol,
                config_summary=self._config_summary(),
                elapsed_seconds=time.time() - t0,
            )

        trades: list[dict] = []
        active_trade: Optional[_ActiveFuturesTrade] = None
        pending_entry: Optional[dict] = None
        cooldown_until: float = 0.0
        total_signals = 0
        skipped_signals = 0
        total_liquidations = 0
        balance = initial_balance

        for i in range(MIN_WARMUP_CANDLES, len(candles)):
            current_candle = candles[i]
            candle_ts = current_candle.timestamp

            # ── 1. Monitor active trade ──
            if active_trade is not None:
                # Liquidation check
                if self._check_liquidation_hit(active_trade, current_candle):
                    trade_dict = self._finalize_liquidation(active_trade, current_candle)
                    balance += trade_dict["pnl_usd"]
                    trades.append(trade_dict)
                    total_liquidations += 1
                    active_trade = None
                    cooldown_until = candle_ts + self._config.cooldown_seconds
                else:
                    result = self._check_exit(active_trade, current_candle)
                    if result is not None:
                        is_partial = result.get("partial", False)

                        if is_partial:
                            # Partial TP1 close — apply PnL but keep trade alive
                            pnl = self._apply_partial_close(active_trade, result, current_candle)
                            balance += pnl
                            # Record a mini-trade for this partial fill
                            trades.append(self._make_partial_trade_dict(active_trade, result, current_candle, pnl))
                        else:
                            # Full close (SL, TP2, or last fraction of TP1)
                            trade_dict = self._finalize_full_trade(active_trade, result, current_candle)
                            balance += trade_dict["pnl_usd"]
                            trades.append(trade_dict)
                            active_trade = None
                            cooldown_until = candle_ts + self._config.cooldown_seconds

            # ── 2. Pending entry ──
            if pending_entry is not None:
                if self._check_entry_fill(pending_entry, current_candle):
                    active_trade = _ActiveFuturesTrade(
                        direction=pending_entry["direction"],
                        entry_price=pending_entry["entry_price"],
                        entry_time=candle_ts,
                        sl=pending_entry["sl"],
                        tp1=pending_entry["tp1"],
                        tp2=pending_entry["tp2"],
                        tp1_fraction=pending_entry["tp1_fraction"],
                        entry_amount=pending_entry["entry_amount"],
                        entry_value=pending_entry["entry_value"],
                        margin_used=pending_entry["margin_used"],
                        liquidation_price=pending_entry["liquidation_price"],
                        leverage=self._leverage,
                    )
                    pending_entry = None
                elif candle_ts - pending_entry["created_at"] > self._config.entry_timeout_seconds:
                    pending_entry = None

            # ── 3. Cooldown ──
            if candle_ts < cooldown_until:
                continue

            # ── 4. Signal generation ──
            if active_trade is None and pending_entry is None:
                snapshot = self._build_snapshot(candles, i, symbol)
                signal = self._signal_engine.generate(snapshot)

                if signal.action != TradeAction.WAIT:
                    total_signals += 1

                if signal.action != TradeAction.WAIT:
                    entry_price = signal.entry
                    sl_price = signal.sl
                    risk_per_unit = abs(entry_price - sl_price)

                    if risk_per_unit <= 0:
                        skipped_signals += 1
                        continue

                    sl_percent = risk_per_unit / entry_price if entry_price > 0 else 0.0
                    risk_capital = self._config.risk_per_trade * balance
                    position_size_usd = risk_capital / sl_percent if sl_percent > 0 else 0.0
                    position_size_usd = min(position_size_usd, balance * self._leverage * 0.95)

                    if position_size_usd < 5.0:
                        skipped_signals += 1
                        continue

                    amount = position_size_usd / entry_price if entry_price > 0 else 0.0
                    margin_used = position_size_usd / self._leverage if self._leverage > 0 else position_size_usd

                    if amount < 0.001 or margin_used > balance * 0.98:
                        skipped_signals += 1
                        continue

                    liquidation_price = self._estimate_liquidation(entry_price, sl_price, self._leverage)

                    sl_distance = abs(entry_price - sl_price)
                    liq_distance = abs(entry_price - liquidation_price)
                    if liq_distance > 0 and sl_distance * 1.5 >= liq_distance:
                        skipped_signals += 1
                        continue

                    pending_entry = {
                        "direction": signal.direction,
                        "entry_price": entry_price,
                        "sl": sl_price,
                        "tp1": signal.tp1,
                        "tp2": signal.tp2,
                        "tp1_fraction": signal.tp1_fraction,
                        "entry_amount": amount,
                        "entry_value": position_size_usd,
                        "margin_used": margin_used,
                        "liquidation_price": liquidation_price,
                        "created_at": candle_ts,
                    }

        # ── End-of-data close ──
        if active_trade is not None:
            final_candle = candles[-1]
            trade_dict = self._finalize_trade_at_end(active_trade, final_candle)
            balance += trade_dict["pnl_usd"]
            trades.append(trade_dict)

        elapsed = time.time() - t0

        from icarus.backtest.metrics import compute_metrics

        metrics = compute_metrics(trades, initial_balance=initial_balance)
        metrics["signals_total"] = total_signals
        metrics["signals_skipped"] = skipped_signals
        metrics["signals_traded"] = len(trades)
        metrics["total_liquidations"] = total_liquidations
        metrics["final_balance"] = balance

        return FuturesBacktestResult(
            symbol=symbol,
            config_summary=self._config_summary(),
            trades=trades,
            metrics=metrics,
            skipped_signals=skipped_signals,
            total_signals=total_signals,
            elapsed_seconds=elapsed,
            total_liquidations=total_liquidations,
        )

    # ═════════════════════════════════════════════════════════════════════
    # Data loading
    # ═════════════════════════════════════════════════════════════════════

    def _load_csv(self, csv_path: Path) -> list[Candle]:
        candles: list[Candle] = []
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                candles.append(Candle(
                    timestamp_ms=int(row["timestamp_ms"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    is_closed=True,
                ))
        logger.debug(f"Loaded {len(candles)} candles from {csv_path.name}")
        return candles

    def _build_snapshot(self, candles, current_idx: int, symbol: str) -> MarketSnapshot:
        start = max(0, current_idx - 199)
        window = tuple(candles[start:current_idx + 1])
        current_candle = candles[current_idx]
        mid = current_candle.close

        spread_amount = mid * SIMULATED_SPREAD
        micro = MicroStructure(
            bid_price=mid - spread_amount / 2,
            ask_price=mid + spread_amount / 2,
            bid_volume=1000.0,
            ask_volume=1000.0,
            mid_price=mid,
            spread=SIMULATED_SPREAD,
            imbalance=SIMULATED_IMBALANCE,
            last_update=current_candle.timestamp,
        )

        return MarketSnapshot(
            symbol=symbol,
            candles=window,
            micro=micro,
            timestamp=current_candle.timestamp,
        )

    # ═════════════════════════════════════════════════════════════════════
    # Liquidation estimation
    # ═════════════════════════════════════════════════════════════════════

    def _estimate_liquidation(self, entry: float, sl: float, leverage: int) -> float:
        if leverage <= 1:
            return sl
        if entry > sl:
            return entry - (entry / leverage) * 0.98
        else:
            return entry + (entry / leverage) * 0.98

    # ═════════════════════════════════════════════════════════════════════
    # Trade monitoring
    # ═════════════════════════════════════════════════════════════════════

    def _check_liquidation_hit(self, trade: _ActiveFuturesTrade, candle: Candle) -> bool:
        liq = trade.liquidation_price
        if liq == 0.0:
            return False
        return candle.low <= liq <= candle.high

    def _check_exit(self, trade: _ActiveFuturesTrade, candle: Candle) -> Optional[dict]:
        high = candle.high
        low = candle.low

        if trade.direction == Direction.LONG:
            if low <= trade.sl:
                return {"reason": "SL", "price": trade.sl}
            # TP1: only if still has unclosed fraction beyond tp1_fraction
            can_take_tp1 = trade.remaining_fraction > (1 - trade.tp1_fraction + 0.001)
            if high >= trade.tp1 and can_take_tp1 and not trade.tp1_already_hit:
                fill_tp1 = min(trade.tp1_fraction, trade.remaining_fraction)
                remaining = trade.remaining_fraction - fill_tp1
                partial = remaining > 0.001
                return {
                    "reason": "TP1", "price": trade.tp1,
                    "fill_fraction": fill_tp1, "partial": partial,
                }
            if high >= trade.tp2:
                return {
                    "reason": "TP2", "price": trade.tp2,
                    "fill_fraction": trade.remaining_fraction, "partial": False,
                }
        else:
            if high >= trade.sl:
                return {"reason": "SL", "price": trade.sl}
            can_take_tp1 = trade.remaining_fraction > (1 - trade.tp1_fraction + 0.001)
            if low <= trade.tp1 and can_take_tp1 and not trade.tp1_already_hit:
                fill_tp1 = min(trade.tp1_fraction, trade.remaining_fraction)
                remaining = trade.remaining_fraction - fill_tp1
                partial = remaining > 0.001
                return {
                    "reason": "TP1", "price": trade.tp1,
                    "fill_fraction": fill_tp1, "partial": partial,
                }
            if low <= trade.tp2:
                return {
                    "reason": "TP2", "price": trade.tp2,
                    "fill_fraction": trade.remaining_fraction, "partial": False,
                }

        return None

    def _check_entry_fill(self, pending: dict, candle: Candle) -> bool:
        entry = pending["entry_price"]
        return candle.low <= entry <= candle.high

    # ═════════════════════════════════════════════════════════════════════
    # Trade finalization
    # ═════════════════════════════════════════════════════════════════════

    def _apply_partial_close(
        self, trade: _ActiveFuturesTrade, exit_info: dict, candle: Candle
    ) -> float:
        """Close a fraction at TP1 and return the PnL. Trade remains active."""
        exit_price = exit_info["price"]
        fill_fraction = exit_info.get("fill_fraction", trade.tp1_fraction)

        amount_closed = trade.entry_amount * fill_fraction
        value_closed = amount_closed * trade.entry_price

        if trade.direction == Direction.LONG:
            gross_pnl = (exit_price - trade.entry_price) * amount_closed
        else:
            gross_pnl = (trade.entry_price - exit_price) * amount_closed

        fee = value_closed * self._config.fee_maker + amount_closed * exit_price * self._config.fee_taker
        pnl = gross_pnl - fee

        trade.remaining_fraction -= fill_fraction
        trade.tp1_already_hit = True

        logger.debug(
            f"Partial TP1: {amount_closed:.3f} units @ {exit_price:.4f}, "
            f"PnL=${pnl:+.4f}, remaining fraction: {trade.remaining_fraction:.3f}"
        )
        return pnl

    def _make_partial_trade_dict(
        self, trade: _ActiveFuturesTrade, exit_info: dict, candle: Candle, pnl: float
    ) -> dict:
        fill_fraction = exit_info.get("fill_fraction", trade.tp1_fraction)
        amount_closed = trade.entry_amount * fill_fraction
        value_closed = amount_closed * trade.entry_price

        return {
            "entry_time": trade.entry_time,
            "exit_time": candle.timestamp,
            "direction": trade.direction.value,
            "entry_price": trade.entry_price,
            "exit_price": exit_info["price"],
            "amount": amount_closed,
            "pnl_usd": round(pnl, 6),
            "pnl_percent": round(pnl / value_closed * 100.0, 4),
            "is_winner": pnl > 0,
            "exit_reason": "TP1",
            "leverage": self._leverage,
            "margin_used": trade.margin_used,
            "liquidation_price": trade.liquidation_price,
        }

    def _finalize_full_trade(
        self, trade: _ActiveFuturesTrade, exit_info: dict, candle: Candle
    ) -> dict:
        """Full close: SL, TP2, or last TP1 fraction."""
        exit_price = exit_info["price"]
        exit_reason = exit_info["reason"]
        fill_fraction = exit_info.get("fill_fraction", 1.0)

        amount_closed = trade.entry_amount * fill_fraction
        value_closed = amount_closed * trade.entry_price

        if trade.direction == Direction.LONG:
            gross_pnl = (exit_price - trade.entry_price) * amount_closed
        else:
            gross_pnl = (trade.entry_price - exit_price) * amount_closed

        fee = value_closed * self._config.fee_maker + amount_closed * exit_price * self._config.fee_taker
        pnl = gross_pnl - fee

        return {
            "entry_time": trade.entry_time,
            "exit_time": candle.timestamp,
            "direction": trade.direction.value,
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "amount": amount_closed,
            "pnl_usd": round(pnl, 6),
            "pnl_percent": round(pnl / value_closed * 100.0, 4),
            "is_winner": pnl > 0,
            "exit_reason": exit_reason,
            "leverage": self._leverage,
            "margin_used": trade.margin_used,
            "liquidation_price": trade.liquidation_price,
        }

    def _finalize_trade_at_end(
        self, trade: _ActiveFuturesTrade, final_candle: Candle
    ) -> dict:
        exit_price = final_candle.close
        amount = trade.entry_amount * trade.remaining_fraction
        value = amount * trade.entry_price

        if trade.direction == Direction.LONG:
            gross_pnl = (exit_price - trade.entry_price) * amount
        else:
            gross_pnl = (trade.entry_price - exit_price) * amount

        fee = value * self._config.fee_maker + amount * exit_price * self._config.fee_taker
        pnl = gross_pnl - fee

        return {
            "entry_time": trade.entry_time,
            "exit_time": final_candle.timestamp,
            "direction": trade.direction.value,
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "amount": amount,
            "pnl_usd": round(pnl, 6),
            "pnl_percent": round(pnl / value * 100.0, 4),
            "is_winner": pnl > 0,
            "exit_reason": "END_OF_DATA",
            "leverage": self._leverage,
            "margin_used": trade.margin_used,
            "liquidation_price": trade.liquidation_price,
        }

    def _finalize_liquidation(
        self, trade: _ActiveFuturesTrade, candle: Candle
    ) -> dict:
        exit_price = trade.liquidation_price
        amount = trade.entry_amount * trade.remaining_fraction
        value = amount * trade.entry_price

        if trade.direction == Direction.LONG:
            gross_pnl = (exit_price - trade.entry_price) * amount
        else:
            gross_pnl = (trade.entry_price - exit_price) * amount

        fee = value * self._config.fee_taker + amount * exit_price * self._config.fee_taker
        pnl = gross_pnl - fee

        logger.warning(
            f"💀 LIQUIDATION at {exit_price:.4f} | PnL: ${pnl:+.2f} | "
            f"Leverage: {self._leverage}x"
        )

        return {
            "entry_time": trade.entry_time,
            "exit_time": candle.timestamp,
            "direction": trade.direction.value,
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "amount": amount,
            "pnl_usd": round(pnl, 6),
            "pnl_percent": round(pnl / value * 100.0, 4),
            "is_winner": False,
            "exit_reason": "LIQUIDATION",
            "leverage": self._leverage,
            "margin_used": trade.margin_used,
            "liquidation_price": trade.liquidation_price,
        }

    # ═════════════════════════════════════════════════════════════════════
    # Config
    # ═════════════════════════════════════════════════════════════════════

    def _config_summary(self) -> dict:
        return {
            "tp1_percent": self._config.tp1_percent,
            "tp2_percent": self._config.tp2_percent,
            "fixed_sl_percent": self._config.fixed_sl_percent,
            "tp1_fraction": self._config.tp1_fraction,
            "threshold_base": self._config.threshold_base,
            "risk_per_trade": self._config.risk_per_trade,
            "kelly_fraction": self._config.kelly_fraction,
            "leverage": self._leverage,
            "margin_mode": self._margin_mode,
            "rsi_oversold": self._config.rsi_oversold,
            "rsi_overbought": self._config.rsi_overbought,
            "volume_surge_threshold": self._config.volume_surge_threshold,
        }


@dataclass
class _ActiveFuturesTrade:
    """Represents an active futures trade during backtest (mutable)."""
    direction: Direction
    entry_price: float
    entry_time: float
    sl: float
    tp1: float
    tp2: float
    tp1_fraction: float
    entry_amount: float
    entry_value: float
    margin_used: float
    liquidation_price: float
    leverage: int
    remaining_fraction: float = 1.0
    tp1_already_hit: bool = False