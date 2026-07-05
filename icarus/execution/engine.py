"""icarus.execution.engine — Moteur d'exécution implémentant l'interface ExecutionEngine.

Refactor depuis execution_engine.py :
  • Implémente l'interface ExecutionEngine (ABC).
  • Utilise les dataclasses du core (OrderRequest, OrderAck, ClosedTrade).
  • Queue asynchrone pour les trades clôturés.
  • Gestion des remplissages partiels.
  • Trailing stop avec activation progressive.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import ccxt

from icarus.config.models import ScalpingConfig, ExchangeConfig
from icarus.core.interfaces import ExecutionEngine as ExecutionEngineInterface, ExchangeError
from icarus.core.types import (
    ClosedTrade,
    ComponentStatus,
    ExitReason,
    HealthStatus,
    MicroStructure,
    OrderAck,
    OrderRequest,
    OrderStatus,
)

logger = logging.getLogger(__name__)


class SpotExecutionController(ExecutionEngineInterface):
    """Moteur d'exécution de base pour le scalping futures.

    Parameters
    ----------
    config: ScalpingConfig
        Configuration de la stratégie (TP, SL, trailing, etc.).
    exchange_cfg: ExchangeConfig
        Configuration de connexion à l'exchange (API keys, sandbox).
    """

    def __init__(self, config: ScalpingConfig, exchange_cfg: ExchangeConfig):
        self._config = config
        self._exchange_cfg = exchange_cfg

        # Paramètres opérationnels
        self._entry_timeout = config.entry_timeout_seconds
        self._trailing_callback = config.trailing_callback
        self._trailing_activation = config.trailing_activation
        self._tp1_fraction = config.tp1_fraction
        self._fee_maker = config.fee_maker
        self._fee_taker = config.fee_taker

        # État interne
        self._lock = asyncio.Lock()
        self._exchange: Optional[ccxt.Exchange] = None

        # Suivi des ordres et trades
        self._pending_orders: Dict[str, Dict] = {}
        self._active_trades: Dict[str, Dict] = {}
        self._trade_details: Dict[str, Dict] = {}

        # File de communication pour les trades clôturés
        self._closed_trades_queue: asyncio.Queue = asyncio.Queue()
        self._last_closed_report: Optional[ClosedTrade] = None

        # Compteur d'IDs
        self._trade_counter = 0

        # Init exchange
        self._init_exchange()

    # ═════════════════════════════════════════════════════════════════════
    # Implémentation de l'interface ExecutionEngine
    # ═════════════════════════════════════════════════════════════════════

    async def place_order(self, order: OrderRequest) -> bool:
        """Place un ordre d'entrée en Limit (Maker).

        Returns
        -------
        True si l'ordre a été accepté par l'exchange, False sinon.
        """
        symbol = self._config.symbol

        # Vérification du solde
        try:
            balance = await self.get_balance("USDT")
            cost = order.amount * order.price
            if cost > balance * 0.95:
                logger.error("[ExecutionController] Solde insuffisant, ordre annulé.")
                return False
        except Exception:
            logger.warning("[ExecutionController] Impossible de vérifier le solde, tentative quand même.")

        logger.info(
            f"[ExecutionController] Tentative {order.side.upper()} | {order.amount} @ {order.price} | "
            f"SL:{order.sl} | TP1:{order.tp1} | TP2:{order.tp2}"
        )

        try:
            result = await self._async_create_order(
                symbol=symbol,
                order_type=order.order_type,
                side=order.side,
                amount=order.amount,
                price=order.price if order.order_type == "limit" else None,
            )
        except Exception as e:
            logger.error(f"[ExecutionController] Échec placement: {e}")
            return False

        # Enregistrement
        async with self._lock:
            self._pending_orders[order.client_id] = {
                "symbol": symbol,
                "side": order.side,
                "order_id": result.get("id"),
                "client_order_id": order.client_id,
                "entry_price": order.price,
                "sl": order.sl,
                "tp1": order.tp1,
                "tp2": order.tp2,
                "amount": order.amount,
                "tp1_fraction": order.tp1_fraction,
                "placed_at": time.time(),
                "created_at": time.time(),
                "status": "pending",
            }

            self._trade_details[order.client_id] = {
                "symbol": symbol,
                "side": order.side,
                "entry": order.price,
                "sl": order.sl,
                "tp1": order.tp1,
                "tp2": order.tp2,
                "tp1_fraction": order.tp1_fraction,
                "total_amount": order.amount,
            }

        return True

    async def monitor_orders(self) -> None:
        """Vérifie les ordres en attente et les trades actifs.

        À appeler à chaque itération (~3-5s).
        """
        # 1. Surveiller les ordres en attente (entrées)
        await self._monitor_pending_orders()

        # 2. Surveiller les trades actifs (TP/SL/Trailing)
        await self._monitor_active_trades()

    async def get_closed_trades(self) -> List[ClosedTrade]:
        """Récupère et vide la file des trades clôturés."""
        reports: List[ClosedTrade] = []
        while not self._closed_trades_queue.empty():
            try:
                report = self._closed_trades_queue.get_nowait()
                reports.append(report)
            except asyncio.QueueEmpty:
                break
        return reports

    async def get_open_positions_count(self) -> int:
        async with self._lock:
            return len(self._active_trades)

    async def get_pending_orders_count(self) -> int:
        async with self._lock:
            return len(self._pending_orders)

    async def get_balance(self, currency: str = "USDT") -> float:
        """Solde disponible pour la devise donnée."""
        balance = await self._async_fetch_balance()
        return float(balance.get(currency, {}).get("free", 0.0))

    async def cancel_all(self) -> int:
        """Annule tous les ordres en attente."""
        count = 0
        pending_copy = dict(self._pending_orders)
        for client_id, data in pending_copy.items():
            try:
                await self._async_cancel_order(data["symbol"], data["order_id"])
                count += 1
            except Exception as e:
                logger.warning(f"[ExecutionController] Échec annulation {client_id}: {e}")
        async with self._lock:
            self._pending_orders.clear()
        return count

    async def close(self) -> None:
        """Ferme la session exchange."""
        if self._exchange:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self._exchange.close)
            except Exception:
                pass
            logger.info("[ExecutionController] Exchange fermé.")

    def get_health(self) -> HealthStatus:
        """Retourne le statut de santé."""
        open_pos = len(self._active_trades)
        pending = len(self._pending_orders)

        return HealthStatus(
            component="ExecutionController",
            status=ComponentStatus.HEALTHY,
            message=f"OK ({open_pos} actifs, {pending} pending)",
            since=time.time(),
            metrics={
                "open_positions": open_pos,
                "pending_orders": pending,
                "total_closed": self._trade_counter,
            },
        )

    # ═════════════════════════════════════════════════════════════════════
    # Exchange (CCXT)
    # ═════════════════════════════════════════════════════════════════════

    def _init_exchange(self) -> None:
        """Initialise la session CCXT."""
        if self._exchange is None:
            exchange_name = self._exchange_cfg.exchange
            exchange_class = getattr(ccxt, exchange_name)
            self._exchange = exchange_class({
                "apiKey": self._exchange_cfg.api_key,
                "secret": self._exchange_cfg.api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            })
            if self._exchange_cfg.sandbox:
                self._exchange.set_sandbox_mode(True)
            logger.info(f"[ExecutionController] Exchange {exchange_name} initialisé.")

    async def _async_create_order(
        self, symbol: str, order_type: str, side: str,
        amount: float, price: Optional[float] = None,
    ) -> Dict:
        """Place un ordre de manière asynchrone (thread pool)."""
        loop = asyncio.get_running_loop()
        if order_type == "limit":
            return await loop.run_in_executor(
                None,
                lambda: self._exchange.create_limit_order(symbol, side, amount, price),
            )
        else:
            return await loop.run_in_executor(
                None,
                lambda: self._exchange.create_market_order(symbol, side, amount),
            )

    async def _async_fetch_order(self, symbol: str, order_id: str) -> Dict:
        """Récupère l'état d'un ordre."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_order(order_id, symbol),
            )
        except Exception:
            return {}

    async def _async_cancel_order(self, symbol: str, order_id: str) -> bool:
        """Annule un ordre."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._exchange.cancel_order(order_id, symbol),
            )
            return "cancel" in str(result).lower()
        except Exception:
            return False

    async def _async_fetch_balance(self) -> Dict:
        """Récupère le solde."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._exchange.fetch_balance)
        except Exception:
            return {}

    # ═════════════════════════════════════════════════════════════════════
    # Surveillance des ordres en attente
    # ═════════════════════════════════════════════════════════════════════

    async def _monitor_pending_orders(self) -> None:
        """Vérifie les ordres d'entrée en attente (timeout, remplissage)."""
        pending_copy = dict(self._pending_orders)

        for client_id, data in pending_copy.items():
            symbol = data["symbol"]
            order_id = data["order_id"]
            placed_at = data["placed_at"]

            # Timeout
            if time.time() - placed_at > self._entry_timeout:
                logger.warning(f"[ExecutionController] Timeout pour {client_id}, annulation.")
                await self._async_cancel_order(symbol, order_id)
                async with self._lock:
                    self._pending_orders.pop(client_id, None)
                continue

            # Vérifier le statut
            order_info = await self._async_fetch_order(symbol, order_id)
            if not order_info:
                order_age = time.time() - data.get("created_at", placed_at)
                if order_age > self._entry_timeout * 2:
                    logger.warning(f"[ExecutionController] Ordre {client_id} trop ancien, annulation forcée.")
                    await self._async_cancel_order(symbol, order_id)
                    async with self._lock:
                        self._pending_orders.pop(client_id, None)
                continue

            status = order_info.get("status")
            filled = order_info.get("filled", 0.0)

            if status == "closed" and filled > 0:
                logger.info(f"[ExecutionController] ✅ Ordre {client_id} rempli ({filled})")
                await self._activate_trade(client_id, filled)
            elif status in ("canceled", "expired"):
                async with self._lock:
                    self._pending_orders.pop(client_id, None)

    async def _activate_trade(self, client_id: str, filled_amount: float) -> None:
        """Active un trade (l'ordre d'entrée est rempli)."""
        async with self._lock:
            data = self._pending_orders.pop(client_id, None)
            if not data:
                return

            trade = {
                "symbol": data["symbol"],
                "side": data["side"],
                "entry_price": data["entry_price"],
                "sl": data["sl"],
                "tp1": data["tp1"],
                "tp2": data["tp2"],
                "total_amount": filled_amount,
                "remaining_amount": filled_amount,
                "exits": [],
                "tp1_triggered": False,
                "tp2_triggered": False,
                "sl_triggered": False,
                "entry_time": time.time(),
                "tp1_amount": filled_amount * data["tp1_fraction"],
                "tp2_amount": filled_amount * (1 - data["tp1_fraction"]),
                "trailing_high": data["entry_price"],
                "trailing_low": data["entry_price"],
            }
            self._active_trades[client_id] = trade

    # ═════════════════════════════════════════════════════════════════════
    # Surveillance des trades actifs (TP/SL/Trailing)
    # ═════════════════════════════════════════════════════════════════════

    async def _monitor_active_trades(self) -> None:
        """Vérifie les conditions de sortie pour tous les trades actifs.

        Utilise le mid_price de la microstructure (passé en paramètre ou
        récupéré via snapshot). Ordre de vérification : SL → TP1 → TP2 → Trailing.
        """
        # On récupère le mid_price via le DataProvider (passé par l'orchestrateur)
        # Pour l'instant, le mid_price vient d'un snapshot passé en argument.
        # Cette méthode est appelée avec un mid_price explicite.
        active_copy = dict(self._active_trades)

        for client_id, trade in active_copy.items():
            # Pour l'instant, on skippe — le mid_price sera passé depuis l'orchestrateur
            # car il dépend du DataProvider.  On garde la structure prête.
            pass

    async def update_with_price(self, mid_price: float) -> None:
        """Vérifie les conditions de sortie avec le mid_price courant.

        Cette méthode remplace _monitor_active_trades() auto-alimenté.
        Elle est appelée par l'orchestrateur à chaque itération avec le
        mid_price le plus récent.

        Parameters
        ----------
        mid_price: float
            Prix milieu du carnet d'ordres (depuis DataProvider).
        """
        active_copy = dict(self._active_trades)

        for client_id, trade in active_copy.items():
            symbol = trade["symbol"]
            side = trade["side"]
            entry = trade["entry_price"]
            sl = trade["sl"]
            tp1 = trade["tp1"]
            tp2 = trade["tp2"]
            remaining = trade["remaining_amount"]

            if remaining <= 0:
                async with self._lock:
                    self._active_trades.pop(client_id, None)
                continue

            # ── Trailing stop ──────────────────────────────────────────
            if side == "buy":
                distance_to_tp1 = tp1 - entry
                progressed = mid_price - entry
                trailing_active = progressed >= self._trailing_activation * distance_to_tp1

                if mid_price > trade["trailing_high"]:
                    trade["trailing_high"] = mid_price
                if trailing_active:
                    trailing_sl = trade["trailing_high"] * (1 - self._trailing_callback)
                    dynamic_sl = max(sl, trailing_sl)
                else:
                    dynamic_sl = sl
            else:  # sell
                distance_to_tp1 = entry - tp1
                progressed = entry - mid_price
                trailing_active = progressed >= self._trailing_activation * distance_to_tp1

                if mid_price < trade["trailing_low"]:
                    trade["trailing_low"] = mid_price
                if trailing_active:
                    trailing_sl = trade["trailing_low"] * (1 + self._trailing_callback)
                    dynamic_sl = min(sl, trailing_sl)
                else:
                    dynamic_sl = sl

            # ── SL ─────────────────────────────────────────────────────
            sl_hit = (side == "buy" and mid_price <= dynamic_sl) or \
                     (side == "sell" and mid_price >= dynamic_sl)

            if sl_hit:
                exit_price = dynamic_sl * (1 - 0.0001 if side == "buy" else 1 + 0.0001)
                pnl = (exit_price - entry) * remaining if side == "buy" else (entry - exit_price) * remaining
                pnl -= remaining * exit_price * self._fee_taker

                trade["exits"].append({"price": exit_price, "amount": remaining, "reason": ExitReason.SL.value})
                trade["remaining_amount"] = 0
                trade["sl_triggered"] = True

                await self._close_trade(client_id, ExitReason.SL)
                continue

            # ── TP1 ────────────────────────────────────────────────────
            if not trade["tp1_triggered"]:
                tp1_hit = (side == "buy" and mid_price >= tp1) or \
                          (side == "sell" and mid_price <= tp1)
                if tp1_hit:
                    close_amount = min(trade["tp1_amount"], trade["remaining_amount"])
                    if close_amount > 0:
                        exit_price = tp1 * (1 - 0.0001 if side == "buy" else 1 + 0.0001)
                        pnl = (exit_price - entry) * close_amount if side == "buy" else (entry - exit_price) * close_amount
                        pnl -= close_amount * exit_price * self._fee_taker

                        trade["exits"].append({"price": exit_price, "amount": close_amount, "reason": ExitReason.TP1.value})
                        trade["remaining_amount"] -= close_amount
                        trade["tp1_triggered"] = True
                        logger.info(f"[ExecutionController] 🎯 TP1: {close_amount} @ {exit_price}")

                        if trade["remaining_amount"] <= 0:
                            await self._close_trade(client_id, ExitReason.TP1)
                            continue

            # ── TP2 ────────────────────────────────────────────────────
            if trade["tp1_triggered"] and not trade["tp2_triggered"] and trade["remaining_amount"] > 0:
                tp2_hit = (side == "buy" and mid_price >= tp2) or \
                          (side == "sell" and mid_price <= tp2)
                if tp2_hit:
                    close_amount = trade["remaining_amount"]
                    exit_price = tp2 * (1 - 0.0001 if side == "buy" else 1 + 0.0001)
                    pnl = (exit_price - entry) * close_amount if side == "buy" else (entry - exit_price) * close_amount
                    pnl -= close_amount * exit_price * self._fee_taker

                    trade["exits"].append({"price": exit_price, "amount": close_amount, "reason": ExitReason.TP2.value})
                    trade["remaining_amount"] = 0
                    trade["tp2_triggered"] = True
                    logger.info(f"[ExecutionController] 🎯 TP2: {close_amount} @ {exit_price}")

                    await self._close_trade(client_id, ExitReason.TP2)

    # ═════════════════════════════════════════════════════════════════════
    # Clôture
    # ═════════════════════════════════════════════════════════════════════

    async def _close_trade(self, client_id: str, reason: ExitReason) -> None:
        """Clôture un trade et publie le rapport."""
        trade = self._active_trades.get(client_id)
        details = self._trade_details.get(client_id, {})

        if not trade or not details:
            return

        side = details.get("side", "buy")
        entry = details.get("entry", 0)
        total_amount = details.get("total_amount", 0)
        exits = trade.get("exits", [])

        # Calcul du PnL pondéré
        pnl_total = 0.0
        weighted_sum = 0.0
        weighted_amount = 0.0

        for e in exits:
            price = e.get("price", 0)
            amount = e.get("amount", 0)
            if side == "buy":
                pnl_total += (price - entry) * amount
            else:
                pnl_total += (entry - price) * amount
            weighted_sum += price * amount
            weighted_amount += amount

        avg_exit = weighted_sum / weighted_amount if weighted_amount > 0 else entry

        # Frais
        total_fees = sum(
            e.get("price", avg_exit) * e.get("amount", 0) * self._fee_taker
            for e in exits
        )
        pnl_after_fees = pnl_total - total_fees

        pnl_pct = (pnl_after_fees / (entry * total_amount)) * 100 if entry * total_amount != 0 else 0

        report = ClosedTrade(
            client_id=client_id,
            symbol=details.get("symbol", ""),
            side=side,
            entry=entry,
            exit=avg_exit,
            amount=total_amount,
            pnl=pnl_after_fees,
            pnl_percent=pnl_pct,
            is_winner=pnl_after_fees > 0,
            reason=reason,
            timestamp=time.time(),
        )

        self._last_closed_report = report
        await self._closed_trades_queue.put(report)

        logger.info(
            f"[ExecutionController] ✅ Trade {client_id} clôturé ({reason.value}). "
            f"PnL: {pnl_after_fees:+.2f} $"
        )

        # Nettoyage
        async with self._lock:
            self._active_trades.pop(client_id, None)
            self._trade_details.pop(client_id, None)

    # ── Générateur d'ID ────────────────────────────────────────────────

    def generate_client_id(self) -> str:
        """Génère un ID client unique."""
        self._trade_counter += 1
        return f"ICARUS_{int(time.time())}_{self._trade_counter}"