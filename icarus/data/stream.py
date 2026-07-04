"""icarus.data.stream — Implémentation de DataProvider pour Binance WebSocket.

Refactoré depuis l'ancien data_stream.py :
  • Implémente l'interface DataProvider (interchangeable).
  • Utilise OhlcvBuffer au lieu d'un deque brut de tuples.
  • Publie les événements CANDLE et MICRO_UPDATE sur l'EventBus.
  • Reconnexion automatique avec backoff exponentiel.
  • Health check intégré (détection stale data).

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import websockets

from icarus.core.interfaces import DataProvider, DataNotReadyError
from icarus.core.types import (
    Candle,
    ComponentStatus,
    HealthStatus,
    MarketSnapshot,
    MicroStructure,
)
from icarus.core.events import EventBus, EventType
from icarus.data.buffer import OhlcvBuffer

logger = logging.getLogger(__name__)


class BinanceDataProvider(DataProvider):
    """Fournisseur de données temps réel via les WebSockets publiques de Binance.

    Parameters
    ----------
    symbol: str
        Paire au format CCXT (ex: "BTC/USDT").
    buffer_size: int
        Nombre de bougies 1m conservées en mémoire (défaut: 200).
    event_bus: EventBus, optional
        Bus d'événements pour publier les mises à jour. Si None, pas de publication.
    stale_timeout: float
        Délai en secondes avant de considérer les données comme "stale" (défaut: 30s).
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        buffer_size: int = 200,
        event_bus: Optional[EventBus] = None,
        stale_timeout: float = 30.0,
    ):
        self._symbol_raw = symbol
        self._symbol_ws = symbol.replace("/", "").lower()

        # URLs WebSocket Binance (publiques, pas besoin d'API key)
        self._kline_url = f"wss://stream.binance.com:9443/ws/{self._symbol_ws}@kline_1m"
        self._depth_url = f"wss://stream.binance.com:9443/ws/{self._symbol_ws}@depth10@100ms"

        # Buffer de bougies (thread-safe)
        self.buffer = OhlcvBuffer(maxlen=buffer_size)

        # Microstructure (mise à jour fréquente, lockée séparément)
        self._micro_lock = asyncio.Lock()
        self._micro = MicroStructure(
            bid_price=0.0, ask_price=0.0,
            bid_volume=0.0, ask_volume=0.0,
            mid_price=0.0, spread=0.0, imbalance=0.5,
            last_update=0.0,
        )

        # Event bus (optionnel)
        self._event_bus = event_bus

        # État de connexion
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._connected = False

        # Health
        self._stale_timeout = stale_timeout
        self._last_health = HealthStatus(
            component="BinanceDataProvider",
            status=ComponentStatus.UNHEALTHY,
            message="Non démarré",
            since=time.time(),
        )

    # ═════════════════════════════════════════════════════════════════════
    # Implémentation de DataProvider
    # ═════════════════════════════════════════════════════════════════════

    async def start(self) -> None:
        """Démarre les deux flux WebSocket en arrière-plan."""
        if self._tasks:
            logger.warning("[BinanceDataProvider] Déjà en cours d'exécution.")
            return

        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._listen_kline()),
            asyncio.create_task(self._listen_depth()),
        ]
        self._connected = True
        logger.info(f"[BinanceDataProvider] Flux démarrés pour {self._symbol_ws}")

    async def stop(self) -> None:
        """Arrête proprement les WebSockets."""
        self._stop_event.set()
        self._connected = False
        if self._tasks:
            # Annuler toutes les tâches
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        logger.info("[BinanceDataProvider] Arrêté.")

    def get_snapshot(self) -> MarketSnapshot:
        """Retourne un snapshot complet du marché.

        Raises
        ------
        DataNotReadyError
            Si le buffer n'a pas assez de bougies.
        """
        if not self.buffer.is_ready:
            raise DataNotReadyError(
                f"Buffer insuffisant: {self.buffer.count} bougies (besoin de 50+)"
            )

        candles = self.buffer.get_all()
        last_candle = candles[-1] if candles else None

        # Vérification stale data
        if self.buffer.age_seconds > self._stale_timeout:
            raise DataNotReadyError(
                f"Données stale: dernière bougie il y a {self.buffer.age_seconds:.0f}s"
            )

        return MarketSnapshot(
            symbol=self._symbol_raw,
            candles=candles,
            micro=self.get_micro_structure(),
            timestamp=time.time(),
        )

    def get_micro_structure(self) -> MicroStructure:
        """Retourne la microstructure courante (lock interne)."""
        # Les dataclasses frozen sont immuables, pas besoin de deep copy
        return self._micro

    @property
    def is_connected(self) -> bool:
        return self._connected and not self._stop_event.is_set()

    # ═════════════════════════════════════════════════════════════════════
    # Listeners WebSocket
    # ═════════════════════════════════════════════════════════════════════

    async def _listen_kline(self) -> None:
        """Écoute le flux des bougies 1m avec reconnexion automatique."""
        retry_delay = 1.0
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self._kline_url) as ws:
                    logger.info(f"[BinanceDataProvider] Connecté au flux Kline pour {self._symbol_ws}")
                    retry_delay = 1.0  # reset après connexion réussie
                    self._connected = True

                    async for message in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            data = json.loads(message)
                            candle = Candle.from_binance_kline(data)
                            is_new = await self.buffer.upsert(candle)

                            # Publier l'événement
                            if self._event_bus:
                                await self._event_bus.publish(EventType.CANDLE, candle)

                            if is_new:
                                logger.debug(f"[Kline] Nouvelle bougie: {candle}")
                        except Exception as parse_err:
                            logger.error(f"[Kline] Erreur parsing: {parse_err}")

            except (websockets.ConnectionClosed, OSError) as e:
                self._connected = False
                logger.warning(f"[Kline] Déconnecté: {e}. Reconnexion dans {retry_delay}s")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)  # backoff exponentiel, max 30s
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                logger.error(f"[Kline] Erreur inattendue: {e}")
                await asyncio.sleep(5)

    async def _listen_depth(self) -> None:
        """Écoute le flux du carnet d'ordres (top 10, toutes les 100ms)."""
        retry_delay = 1.0
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self._depth_url) as ws:
                    logger.info(f"[BinanceDataProvider] Connecté au flux Depth pour {self._symbol_ws}")
                    retry_delay = 1.0

                    async for message in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            data = json.loads(message)
                            micro = MicroStructure.from_binance_depth(data)
                            if micro is not None:
                                async with self._micro_lock:
                                    self._micro = micro

                                # Publier l'événement
                                if self._event_bus:
                                    await self._event_bus.publish(EventType.MICRO_UPDATE, micro)
                        except Exception as parse_err:
                            logger.error(f"[Depth] Erreur parsing: {parse_err}")

            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"[Depth] Déconnecté: {e}. Reconnexion dans {retry_delay}s")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Depth] Erreur inattendue: {e}")
                await asyncio.sleep(5)

    # ═════════════════════════════════════════════════════════════════════
    # Health
    # ═════════════════════════════════════════════════════════════════════

    def get_health(self) -> HealthStatus:
        """Retourne le statut de santé du DataProvider."""
        age = self.buffer.age_seconds
        connected = self.is_connected

        if not connected:
            status = ComponentStatus.UNHEALTHY
            msg = "Non connecté"
        elif age > self._stale_timeout:
            status = ComponentStatus.DEGRADED
            msg = f"Données stale ({age:.0f}s sans mise à jour)"
        elif age > self._stale_timeout * 0.5:
            status = ComponentStatus.DEGRADED
            msg = f"Données légèrement retardées ({age:.0f}s)"
        else:
            status = ComponentStatus.HEALTHY
            msg = f"OK ({self.buffer.count} bougies, age={age:.1f}s)"

        return HealthStatus(
            component="BinanceDataProvider",
            status=status,
            message=msg,
            since=time.time(),
            metrics={
                "candles_count": self.buffer.count,
                "age_seconds": age,
                "symbol": self._symbol_raw,
                "connected": connected,
            },
        )