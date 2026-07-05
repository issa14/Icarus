"""icarus.backtest.engine — Moteur de backtesting réutilisant les vrais modules.

Le BacktestEngine est un consommateur de l'interface SignalEngine :
  → Il injecte des MarketSnapshot (construits depuis des CSV) dans le SignalEngine
  → Il n'utilise PAS le RiskEngine ni l'ExecutionEngine réels
  → La simulation d'exécution est faite en interne (SL/TP coché bougie par bougie)

Cela garantit que les paramètres optimisés en backtest sont DIRECTEMENT
transposables en conditions réelles (même code path pour les signaux).

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from icarus.config.models import ScalpingConfig
from icarus.core.types import (
    Candle,
    Direction,
    MarketSnapshot,
    MicroStructure,
    TradeAction,
    TradingSignal,
)
from icarus.signal.engine import ScalpingSignalEngine

logger = logging.getLogger(__name__)

# ── Paramètres de simulation (constants) ─────────────────────────────────
SIMULATED_SPREAD = 0.0002      # 0.02% (match Binance futures)
SIMULATED_IMBALANCE = 0.5       # neutre
SIMULATED_BID_ASK_RATIO = 0.0001  # bid_vol / ask_vol
FEE_TAKER = 0.0004              # 0.04% (Binance taker)
FEE_MAKER = 0.0002              # 0.02% (Binance maker)
MIN_WARMUP_CANDLES = 50          # minimum de bougies pour les indicateurs


@dataclass
class BacktestResult:
    """Résultat complet d'un backtest."""
    symbol: str
    config_summary: dict
    trades: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    skipped_signals: int = 0
    total_signals: int = 0
    elapsed_seconds: float = 0.0


class BacktestEngine:
    """Moteur de backtesting qui réutilise ScalpingSignalEngine.

    Parameters
    ----------
    config: ScalpingConfig
        Configuration validée (paramètres TP/SL, filtres, etc.).
    """

    def __init__(self, config: ScalpingConfig):
        self._config = config
        self._signal_engine = ScalpingSignalEngine(config)

    # ═════════════════════════════════════════════════════════════════════
    # API publique
    # ═════════════════════════════════════════════════════════════════════

    def run(
        self,
        csv_path: Path,
        symbol: str,
        initial_balance: float = 100.0,
        max_trades: Optional[int] = None,
    ) -> BacktestResult:
        """Exécute un backtest complet sur un fichier CSV.

        Parameters
        ----------
        csv_path: Path
            Chemin vers le CSV (format: timestamp_ms,open,high,low,close,volume).
        symbol: str
            Symbole (ex: "SOL/USDT").
        initial_balance: float
            Capital initial en USDT.
        max_trades: Optional[int]
            Nombre max de trades à simuler (None = pas de limite).

        Returns
        -------
        BacktestResult avec trades, métriques, stats.
        """
        t0 = time.time()

        candles = self._load_csv(csv_path)
        if len(candles) < MIN_WARMUP_CANDLES + 10:
            logger.error(
                f"Pas assez de bougies ({len(candles)}) — minimum {MIN_WARMUP_CANDLES + 10}"
            )
            return BacktestResult(
                symbol=symbol,
                config_summary=self._config_summary(),
                elapsed_seconds=time.time() - t0,
            )

        trades: list[dict] = []
        active_trade: Optional[_ActiveTrade] = None
        pending_entry: Optional[dict] = None
        cooldown_until: float = 0.0
        total_signals = 0
        skipped_signals = 0
        balance = initial_balance

        # Itération bougie par bougie (à partir de l'index 50)
        for i in range(MIN_WARMUP_CANDLES, len(candles)):
            current_candle = candles[i]
            candle_ts = current_candle.timestamp

            # ── 1. Surveiller un trade actif ──
            if active_trade is not None:
                result = self._check_exit(active_trade, current_candle, i)
                if result is not None:
                    # Trade clôturé
                    trade_dict = self._finalize_trade(
                        active_trade, result, current_candle, balance
                    )
                    balance += trade_dict["pnl_usd"]
                    trades.append(trade_dict)
                    active_trade = None
                    cooldown_until = candle_ts + self._config.cooldown_seconds

            # ── 2. Entry en attente (limit order non fillé) ──
            if pending_entry is not None:
                if self._check_entry_fill(pending_entry, current_candle):
                    active_trade = _ActiveTrade(
                        direction=pending_entry["direction"],
                        entry_price=pending_entry["entry_price"],
                        entry_time=candle_ts,
                        sl=pending_entry["sl"],
                        tp1=pending_entry["tp1"],
                        tp2=pending_entry["tp2"],
                        tp1_fraction=pending_entry["tp1_fraction"],
                        entry_amount=pending_entry["entry_amount"],
                        entry_value=pending_entry["entry_value"],
                    )
                    pending_entry = None
                elif candle_ts - pending_entry["created_at"] > self._config.entry_timeout_seconds:
                    # Timeout — annuler l'entry
                    pending_entry = None

            # ── 3. Cooldown actif ? ──
            if candle_ts < cooldown_until:
                continue

            # ── 4. Pas de trade actif → chercher un signal ──
            if active_trade is None and pending_entry is None:
                snapshot = self._build_snapshot(candles, i, symbol)
                signal = self._signal_engine.generate(snapshot)

                if signal.action != TradeAction.WAIT:
                    total_signals += 1

                if signal.action != TradeAction.WAIT:
                    # Appliquer les filtres de risque (simplifiés pour le backtest)
                    risk_per_trade = self._config.risk_per_trade * initial_balance
                    entry_price = signal.entry
                    sl_price = signal.sl
                    risk_per_unit = abs(entry_price - sl_price)

                    if risk_per_unit <= 0:
                        skipped_signals += 1
                        continue

                    # Calcul de la taille de position
                    position_size_usd = risk_per_trade / self._config.fixed_sl_percent
                    position_size_usd = min(position_size_usd, initial_balance * 0.95)

                    amount = position_size_usd / entry_price if entry_price > 0 else 0.0

                    if amount * entry_price < 5.0:  # minimum Binance spot
                        skipped_signals += 1
                        continue

                    # Créer une entry en attente
                    pending_entry = {
                        "direction": signal.direction,
                        "entry_price": entry_price,
                        "sl": sl_price,
                        "tp1": signal.tp1,
                        "tp2": signal.tp2,
                        "tp1_fraction": signal.tp1_fraction,
                        "entry_amount": amount,
                        "entry_value": amount * entry_price,
                        "created_at": candle_ts,
                    }

        # ── Clôturer un trade resté ouvert à la fin ──
        if active_trade is not None:
            final_candle = candles[-1]
            trade_dict = self._finalize_trade_at_end(active_trade, final_candle, balance)
            balance += trade_dict["pnl_usd"]
            trades.append(trade_dict)
            active_trade = None

        elapsed = time.time() - t0

        # Calculer les métriques
        from icarus.backtest.metrics import compute_metrics

        metrics = compute_metrics(trades, initial_balance=initial_balance)
        metrics["signals_total"] = total_signals
        metrics["signals_skipped"] = skipped_signals
        metrics["signals_traded"] = len(trades)

        return BacktestResult(
            symbol=symbol,
            config_summary=self._config_summary(),
            trades=trades,
            metrics=metrics,
            skipped_signals=skipped_signals,
            total_signals=total_signals,
            elapsed_seconds=elapsed,
        )

    # ═════════════════════════════════════════════════════════════════════
    # Helpers privés
    # ═════════════════════════════════════════════════════════════════════

    def _load_csv(self, csv_path: Path) -> list[Candle]:
        """Charge un CSV OHLCV en liste de Candle."""
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
        logger.debug(f"Chargé {len(candles)} bougies depuis {csv_path.name}")
        return candles

    def _build_snapshot(
        self, candles: list[Candle], current_idx: int, symbol: str
    ) -> MarketSnapshot:
        """Construit un MarketSnapshot pour la bougie courante."""
        # Prendre les 200 dernières bougies (ou toutes si < 200)
        start = max(0, current_idx - 199)
        window = tuple(candles[start:current_idx + 1])

        current_candle = candles[current_idx]
        mid = current_candle.close

        # Microstructure simulée
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

    def _check_exit(
        self, trade: _ActiveTrade, candle: Candle, _idx: int
    ) -> Optional[dict]:
        """Vérifie si le trade doit être clôturé sur cette bougie.

        Returns dict avec exit_reason, exit_price, ou None si toujours actif.
        """
        high = candle.high
        low = candle.low

        if trade.direction == Direction.LONG:
            # Stop-loss
            if low <= trade.sl:
                return {"reason": "SL", "price": trade.sl}
            # TP1
            if high >= trade.tp1 and trade.remaining_fraction > 1 - trade.tp1_fraction:
                # Clôture partielle au TP1
                fill_tp1 = min(trade.tp1_fraction, trade.remaining_fraction)
                return {
                    "reason": "TP1",
                    "price": trade.tp1,
                    "fill_fraction": fill_tp1,
                    "partial": trade.remaining_fraction - fill_tp1 > 0.001,
                }
            # TP2
            if high >= trade.tp2:
                return {"reason": "TP2", "price": trade.tp2, "fill_fraction": trade.remaining_fraction}
        else:
            # SHORT
            if high >= trade.sl:
                return {"reason": "SL", "price": trade.sl}
            if low <= trade.tp1 and trade.remaining_fraction > 1 - trade.tp1_fraction:
                fill_tp1 = min(trade.tp1_fraction, trade.remaining_fraction)
                return {
                    "reason": "TP1",
                    "price": trade.tp1,
                    "fill_fraction": fill_tp1,
                    "partial": trade.remaining_fraction - fill_tp1 > 0.001,
                }
            if low <= trade.tp2:
                return {"reason": "TP2", "price": trade.tp2, "fill_fraction": trade.remaining_fraction}

        return None

    def _check_entry_fill(self, pending: dict, candle: Candle) -> bool:
        """Simule le fill d'un ordre limite."""
        # L'ordre limit est fillé si le prix traverse le niveau d'entry
        entry = pending["entry_price"]
        direction = pending["direction"]

        if direction == Direction.LONG:
            return candle.low <= entry <= candle.high
        else:
            return candle.low <= entry <= candle.high

    def _finalize_trade(
        self,
        trade: _ActiveTrade,
        exit_info: dict,
        _candle: Candle,
        current_balance: float,
    ) -> dict:
        """Finalise un trade clôturé et retourne le dict trade."""
        exit_price = exit_info["price"]
        exit_reason = exit_info["reason"]
        fill_fraction = exit_info.get("fill_fraction", 1.0)

        # Si sortie partielle, ajuster
        partial = exit_info.get("partial", False)

        if partial:
            # Clôturer la fraction TP1, garder le reste
            amount_closed = trade.entry_amount * fill_fraction
            value_closed = amount_closed * trade.entry_price

            if trade.direction == Direction.LONG:
                gross_pnl = (exit_price - trade.entry_price) * amount_closed
            else:
                gross_pnl = (trade.entry_price - exit_price) * amount_closed

            fee = value_closed * FEE_TAKER + amount_closed * exit_price * FEE_TAKER
            pnl = gross_pnl - fee

            # Mettre à jour le trade actif
            trade.remaining_fraction -= fill_fraction
            trade.tp1_already_hit = True

            # Si plus rien, c'est vraiment fermé
            if trade.remaining_fraction < 0.001:
                return {
                    "entry_time": trade.entry_time,
                    "exit_time": _candle.timestamp if '_candle' in dir() else 0,
                    "direction": trade.direction.value,
                    "entry_price": trade.entry_price,
                    "exit_price": exit_price,
                    "amount": trade.entry_amount,
                    "pnl_usd": pnl,
                    "pnl_pct": pnl / trade.entry_value * 100.0,
                    "is_winner": pnl > 0,
                    "exit_reason": exit_reason,
                }

        else:
            # Clôture complète
            if trade.direction == Direction.LONG:
                gross_pnl = (exit_price - trade.entry_price) * trade.entry_amount
            else:
                gross_pnl = (trade.entry_price - exit_price) * trade.entry_amount

            fee = trade.entry_value * FEE_TAKER + trade.entry_amount * exit_price * FEE_TAKER
            pnl = gross_pnl - fee

            return {
                "entry_time": trade.entry_time,
                "exit_time": _candle.timestamp if '_candle' in dir() else 0,
                "direction": trade.direction.value,
                "entry_price": trade.entry_price,
                "exit_price": exit_price,
                "amount": trade.entry_amount,
                "pnl_usd": pnl,
                "pnl_pct": pnl / trade.entry_value * 100.0,
                "is_winner": pnl > 0,
                "exit_reason": exit_reason,
            }

        # Fallback (ne devrait jamais arriver)
        return {
            "entry_time": trade.entry_time,
            "exit_time": 0,
            "direction": trade.direction.value,
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "amount": trade.entry_amount,
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
            "is_winner": False,
            "exit_reason": exit_reason,
        }

    def _finalize_trade_at_end(
        self, trade: _ActiveTrade, final_candle: Candle, current_balance: float
    ) -> dict:
        """Force la clôture d'un trade resté ouvert à la fin des données."""
        exit_price = final_candle.close

        if trade.direction == Direction.LONG:
            gross_pnl = (exit_price - trade.entry_price) * trade.entry_amount
        else:
            gross_pnl = (trade.entry_price - exit_price) * trade.entry_amount

        fee = trade.entry_value * FEE_TAKER + trade.entry_amount * exit_price * FEE_TAKER
        pnl = gross_pnl - fee

        return {
            "entry_time": trade.entry_time,
            "exit_time": final_candle.timestamp,
            "direction": trade.direction.value,
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "amount": trade.entry_amount,
            "pnl_usd": round(pnl, 6),
            "pnl_pct": round(pnl / trade.entry_value * 100.0, 4),
            "is_winner": pnl > 0,
            "exit_reason": "END_OF_DATA",
        }

    def _config_summary(self) -> dict:
        """Résumé de la configuration pour le rapport."""
        return {
            "tp1_percent": self._config.tp1_percent,
            "tp2_percent": self._config.tp2_percent,
            "fixed_sl_percent": self._config.fixed_sl_percent,
            "tp1_fraction": self._config.tp1_fraction,
            "threshold_base": self._config.threshold_base,
            "risk_per_trade": self._config.risk_per_trade,
            "kelly_fraction": self._config.kelly_fraction,
            "rsi_oversold": self._config.rsi_oversold,
            "rsi_overbought": self._config.rsi_overbought,
            "volume_surge_threshold": self._config.volume_surge_threshold,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Trade actif (état mutable interne au backtest)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _ActiveTrade:
    """Représente un trade en cours pendant le backtest (mutable)."""
    direction: Direction
    entry_price: float
    entry_time: float
    sl: float
    tp1: float
    tp2: float
    tp1_fraction: float
    entry_amount: float
    entry_value: float
    remaining_fraction: float = 1.0
    tp1_already_hit: bool = False