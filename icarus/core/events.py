"""icarus.core.events — Bus d'événements asynchrone (pub/sub).

Remplace le polling dans la boucle principale par une communication événementielle.
Chaque module peut publier et souscrire à des événements de manière découplée.

═══════════════════════════════════════════════════════════════════════════════
UTILISATION
═══════════════════════════════════════════════════════════════════════════════

    from icarus.core.events import EventBus, EventType

    bus = EventBus()

    async def on_candle(snapshot, timestamp):
        print(f"Nouvelle bougie reçue")

    bus.subscribe(EventType.CANDLE, on_candle)
    await bus.publish(EventType.CANDLE, snapshot)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from enum import StrEnum, auto
from typing import Any, Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)

# Type d'un callback : async function prenant (data, timestamp)
Callback = Callable[[Any, float], Awaitable[None]]


class EventType(StrEnum):
    """Types d'événements échangés entre les modules."""

    # ── Data Layer ──
    CANDLE           = auto()   # Nouvelle Candle disponible
    MICRO_UPDATE     = auto()   # Mise à jour de la microstructure (depth)

    # ── Signal Layer ──
    SIGNAL_GENERATED = auto()   # Un TradingSignal a été produit

    # ── Risk Layer ──
    SIGNAL_VALIDATED  = auto()  # Signal validé par le RiskEngine
    SIGNAL_REJECTED   = auto()  # Signal rejeté (raison fournie)
    CIRCUIT_BREAKER   = auto()  # Circuit breaker déclenché ou libéré

    # ── Execution Layer ──
    ORDER_PLACED     = auto()   # Ordre envoyé à l'exchange
    ORDER_FILLED     = auto()   # Ordre complètement rempli
    ORDER_PARTIAL    = auto()   # Ordre partiellement rempli
    ORDER_CANCELLED  = auto()   # Ordre annulé (timeout ou manuel)
    ORDER_REJECTED   = auto()   # Ordre rejeté par l'exchange
    POSITION_OPENED  = auto()   # Trade actif ouvert
    POSITION_CLOSED  = auto()   # Trade clôturé (ClosedTrade disponible)

    # ── System ──
    HEALTH_CHECK     = auto()   # Demande de health check global
    COMPONENT_HEALTH = auto()   # Health status d'un composant
    SHUTDOWN         = auto()   # Signal d'arrêt du bot
    ERROR            = auto()   # Erreur critique remontée


class EventBus:
    """Bus d'événements asynchrone à publication/souscription.

    Thread-safe (toutes les opérations passent par un lock asyncio).
    Chaque événement est délivré à tous les souscripteurs en parallèle.
    """

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callback]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._stats: Dict[str, int] = {
            "events_published": 0,
            "callbacks_fired": 0,
            "errors_caught": 0,
        }

    # ── Souscription ───────────────────────────────────────────────────

    async def subscribe(self, event: EventType, callback: Callback) -> None:
        """Enregistre un callback pour un type d'événement.

        Le callback doit être une coroutine prenant ``(data, timestamp)``.
        Il est appelé de manière asynchrone (pas de garantie d'ordre).
        """
        async with self._lock:
            if callback not in self._subscribers[event]:
                self._subscribers[event].append(callback)
                logger.debug(f"[EventBus] Souscription à '{event}': {callback.__name__}")

    async def unsubscribe(self, event: EventType, callback: Callback) -> None:
        """Retire un callback précédemment enregistré."""
        async with self._lock:
            try:
                self._subscribers[event].remove(callback)
                logger.debug(f"[EventBus] Désabonnement de '{event}': {callback.__name__}")
            except ValueError:
                pass

    # ── Publication ─────────────────────────────────────────────────────

    async def publish(self, event: EventType, data: Any = None) -> None:
        """Publie un événement. Tous les souscripteurs sont notifiés en parallèle.

        Les erreurs dans les callbacks sont loggées mais ne bloquent pas
        les autres souscripteurs.

        Parameters
        ----------
        event: EventType
            Le type d'événement.
        data: Any, optional
            Données associées à l'événement (ex: MarketSnapshot, ClosedTrade, …).
        """
        timestamp = time.time()
        async with self._lock:
            callbacks = list(self._subscribers.get(event, []))

        if not callbacks:
            return

        self._stats["events_published"] += 1
        self._stats["callbacks_fired"] += len(callbacks)

        # Exécution parallèle de tous les callbacks
        tasks = []
        for cb in callbacks:
            task = asyncio.create_task(self._safe_invoke(cb, data, timestamp, event))
            tasks.append(task)

        # On attend que tous les callbacks aient terminé (ou échoué)
        # pour éviter que des tâches orphelines ne s'accumulent.
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_invoke(self, callback: Callback, data: Any, timestamp: float, event: EventType) -> None:
        """Invoque un callback en capturant les exceptions silencieusement."""
        try:
            await callback(data, timestamp)
        except Exception:
            self._stats["errors_caught"] += 1
            logger.exception(
                f"[EventBus] Erreur dans le callback '{callback.__name__}' "
                f"pour l'événement '{event}'"
            )

    # ── Stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        """Retourne les statistiques du bus (nombre d'événements, erreurs…)."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Remet les compteurs de statistiques à zéro."""
        self._stats = {k: 0 for k in self._stats}