"""Contrôleur de risque futures pour le bot Icarus."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple

from icarus.config.models import ScalpingConfig
from icarus.core.interfaces import RiskEngine as RiskEngineInterface
from icarus.core.types import (
    BotStats,
    ClosedTrade,
    ComponentStatus,
    ExitReason,
    HealthStatus,
    PositionSize,
    TradingSignal,
    ValidatedSignal,
)

logger = logging.getLogger(__name__)


class FuturesRiskController(RiskEngineInterface):
    """Contrôleur de risque pour trading futures."""

    def __init__(self, config: ScalpingConfig, db_path: str = "icarus_risk.db"):
        self._config = config
        self._db_path = db_path

        self._risk_per_trade = config.risk_per_trade
        self._max_daily_loss = config.max_daily_loss_percent
        self._max_positions = config.max_positions
        self._kelly_fraction = config.kelly_fraction
        self._target_win_rate = 0.60
        self._target_rr = 1.0

        self._recent_results: Deque[bool] = deque(maxlen=10)
        self._consecutive_loss_halt_until: float = 0.0
        self._daily_pnl: float = 0.0
        self._day_start_balance: Optional[float] = None
        self._is_halted: bool = False

        self._stats = BotStats()

        self._init_db()
        self._load_daily_pnl()

    def validate_and_size(
        self,
        signal: TradingSignal,
        current_balance: float,
        open_positions: int,
    ) -> ValidatedSignal:
        halted, reason = self.check_circuit_breaker(current_balance)
        if halted:
            return ValidatedSignal(
                signal=signal,
                position=PositionSize(0.0, 0.0, 0.0, 0.0, 0.0),
                reason=reason,
                is_valid=False,
            )

        if open_positions >= self._max_positions:
            return ValidatedSignal(
                signal=signal,
                position=PositionSize(0.0, 0.0, 0.0, 0.0, 0.0),
                reason=f"Max positions atteint ({self._max_positions})",
                is_valid=False,
            )

        if signal.action.value not in ("BUY", "SELL"):
            return ValidatedSignal(
                signal=signal,
                position=PositionSize(0.0, 0.0, 0.0, 0.0, 0.0),
                reason=f"Action invalide: {signal.action}",
                is_valid=False,
            )

        pos = self._calculate_futures_position(current_balance, signal.entry, signal.sl)
        if pos.amount == 0.0:
            return ValidatedSignal(
                signal=signal,
                position=pos,
                reason="Taille de position nulle (Kelly négatif, levier trop élevé ou SL trop serré)",
                is_valid=False,
            )

        logger.info(
            f"[FuturesRiskController] ✅ Signal validé | Taille: {pos.amount} | "
            f"Risque: {pos.risk_usd:.2f} $ ({pos.risk_pct:.2f}% du capital) | marge: {pos.margin_usd:.2f} $"
        )

        return ValidatedSignal(
            signal=signal,
            position=pos,
            reason="Validation OK",
            is_valid=True,
        )

    def check_circuit_breaker(self, current_balance: float) -> Tuple[bool, str]:
        if self._day_start_balance is None:
            self._day_start_balance = current_balance
            self._daily_pnl = 0.0
            self._save_daily_pnl()
            logger.info(f"[FuturesRiskController] Capital initial du jour: {current_balance:.2f} $")
            return False, ""

        now = time.time()
        if self._consecutive_loss_halt_until and now < self._consecutive_loss_halt_until:
            remaining = int(self._consecutive_loss_halt_until - now)
            return True, f"Halt temporaire ({remaining}s restantes)"

        loss_pct = -self._daily_pnl / self._day_start_balance if self._day_start_balance > 0 else 0.0
        if loss_pct >= self._max_daily_loss:
            self._is_halted = True
            return True, f"Circuit breaker ! Perte jour: {loss_pct*100:.2f}% (max {self._max_daily_loss*100:.2f}%)"

        self._is_halted = False
        return False, ""

    def update_pnl(self, pnl: float, *, is_winner: Optional[bool] = None) -> None:
        if is_winner is None:
            is_winner = pnl > 0

        self._daily_pnl += pnl
        self._stats.total_pnl += pnl
        self._stats.daily_pnl = self._daily_pnl

        if is_winner:
            self._stats.win_count += 1
        else:
            self._stats.loss_count += 1

        self._recent_results.append(bool(is_winner))
        consecutive = self._count_consecutive_losses()
        if consecutive >= 4:
            self._consecutive_loss_halt_until = time.time() + 30 * 60
            logger.warning(f"[FuturesRiskController] {consecutive} pertes consécutives → halt 30min")

        self._save_daily_pnl()
        logger.info(f"[FuturesRiskController] PnL mis à jour: {self._daily_pnl:.2f} $")

    def record_trade(self, trade: ClosedTrade) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trades_history
                (timestamp, symbol, direction, entry, exit, pnl, pnl_percent, is_winner)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(trade.timestamp),
                    trade.symbol,
                    trade.side,
                    trade.entry,
                    trade.exit,
                    trade.pnl,
                    trade.pnl_percent,
                    1 if trade.is_winner else 0,
                ),
            )
            conn.commit()
            conn.close()
            self.update_pnl(trade.pnl, is_winner=trade.is_winner)
            logger.info(f"[FuturesRiskController] Trade enregistré: {trade.symbol} {trade.side} | PnL: {trade.pnl:.2f} $ | Win: {trade.is_winner}")
        except Exception as e:
            logger.error(f"[FuturesRiskController] Erreur enregistrement trade: {e}")

    def get_stats(self) -> BotStats:
        self._stats.daily_pnl = self._daily_pnl
        return self._stats

    def get_health(self) -> HealthStatus:
        if self._is_halted:
            status = ComponentStatus.DEGRADED
            msg = "Circuit breaker actif"
        else:
            status = ComponentStatus.HEALTHY
            msg = f"OK (PnL jour: {self._daily_pnl:+.2f} $)"

        return HealthStatus(
            component="FuturesRiskController",
            status=status,
            message=msg,
            since=time.time(),
            metrics={
                "daily_pnl": self._daily_pnl,
                "is_halted": self._is_halted,
                "consecutive_losses": self._count_consecutive_losses(),
                "win_rate_dynamic": self._get_dynamic_win_rate(),
            },
        )

    @property
    def max_positions(self) -> int:
        return self._max_positions

    def _calculate_futures_position(
        self, balance: float, entry: float, sl: float
    ) -> PositionSize:
        if entry <= 0 or sl <= 0 or entry == sl:
            return PositionSize(0.0, 0.0, 0.0, 0.0, 0.0)

        win_rate = self._get_dynamic_win_rate()
        kelly_full = (win_rate * self._target_rr - (1 - win_rate)) / self._target_rr
        kelly_full = max(0.0, kelly_full)
        kelly_used = kelly_full * self._kelly_fraction

        max_risk_usd = balance * kelly_used
        risk_per_unit = abs(entry - sl)
        amount = max_risk_usd / risk_per_unit if risk_per_unit > 0 else 0.0

        margin = amount * entry / self._config.leverage
        if margin <= 0 or margin > balance:
            return PositionSize(0.0, 0.0, 0.0, 0.0, 0.0)

        if amount < 0.001:
            logger.warning("[FuturesRiskController] Taille de position trop petite (<0.001), trade ignoré.")
            return PositionSize(0.0, 0.0, 0.0, 0.0, 0.0)

        if abs(entry - sl) * 1.5 >= abs(entry - self._estimate_liquidation(entry, sl)):
            logger.warning("[FuturesRiskController] SL trop proche de la liquidation, trade rejeté.")
            return PositionSize(0.0, 0.0, 0.0, 0.0, 0.0)

        exposure = amount * entry
        risk_usd = amount * risk_per_unit
        risk_pct = (risk_usd / balance) * 100 if balance > 0 else 0.0

        return PositionSize(
            amount=round(amount, 3),
            exposure_usd=exposure,
            risk_usd=risk_usd,
            risk_pct=risk_pct,
            kelly_fraction=kelly_used,
            margin_usd=margin,
        )

    def _estimate_liquidation(self, entry: float, sl: float) -> float:
        # Estimation simplifiée : la liquidation réelle dépendra de l'exchange.
        if self._config.leverage <= 1:
            return sl
        if entry > sl:
            return entry - (entry / self._config.leverage) * 0.98
        return entry + (entry / self._config.leverage) * 0.98

    def _get_dynamic_win_rate(self) -> float:
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN is_winner = 1 THEN 1 ELSE 0 END) as wins
                FROM trades_history
                WHERE timestamp > ?
                """,
                (int(time.time()) - 30 * 86400,),
            )
            row = cursor.fetchone()
            conn.close()

            if row and row[0] >= 30:
                observed = row[1] / row[0] if row[0] > 0 else 0.0
                return 0.7 * observed + 0.3 * self._target_win_rate
        except Exception as e:
            logger.warning(f"[FuturesRiskController] Erreur lecture win rate: {e}")

        return self._target_win_rate

    def _count_consecutive_losses(self) -> int:
        cnt = 0
        for v in reversed(self._recent_results):
            if not v:
                cnt += 1
            else:
                break
        return cnt

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                starting_balance REAL NOT NULL,
                current_pnl REAL NOT NULL DEFAULT 0.0,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                exit REAL NOT NULL,
                pnl REAL NOT NULL,
                pnl_percent REAL NOT NULL,
                is_winner BOOLEAN NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()
        logger.info("[FuturesRiskController] Base de données initialisée.")

    def _load_daily_pnl(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT starting_balance, current_pnl FROM daily_pnl WHERE date = ? ORDER BY id DESC LIMIT 1",
                (today,),
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                self._day_start_balance = row[0]
                self._daily_pnl = row[1]
                logger.info(
                    f"[FuturesRiskController] PnL du jour chargé: {self._daily_pnl:.2f} $ "
                    f"(capital départ: {self._day_start_balance:.2f} $)"
                )
            else:
                self._day_start_balance = None
                self._daily_pnl = 0.0
        except Exception as e:
            logger.warning(f"[FuturesRiskController] Erreur chargement PnL: {e}")
            self._day_start_balance = None
            self._daily_pnl = 0.0

    def _save_daily_pnl(self) -> None:
        if self._day_start_balance is None:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM daily_pnl WHERE date = ?", (today,))
            exists = cursor.fetchone()
            if exists:
                cursor.execute(
                    "UPDATE daily_pnl SET current_pnl = ?, last_update = CURRENT_TIMESTAMP WHERE date = ?",
                    (self._daily_pnl, today),
                )
            else:
                cursor.execute(
                    "INSERT INTO daily_pnl (date, starting_balance, current_pnl) VALUES (?, ?, ?)",
                    (today, self._day_start_balance, self._daily_pnl),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[FuturesRiskController] Erreur sauvegarde PnL: {e}")
