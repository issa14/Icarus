"""icarus.data.buffer — Buffer OHLCV thread-safe avec deque.

Extrait de l'ancien DataStream pour séparer la responsabilité de stockage
de celle de la connexion WebSocket.

Le buffer est thread-safe (lock asyncio) car il est accédé à la fois par
le listener WebSocket (écriture) et par le SignalEngine (lecture).

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque, List, Optional, Tuple

from icarus.core.types import Candle


class OhlcvBuffer:
    """Buffer circulaire de bougies OHLCV 1 minute.

    Thread-safe (asyncio.Lock). Utilisé par le DataStream pour stocker
    les bougies reçues via WebSocket.

    Parameters
    ----------
    maxlen: int
        Nombre maximum de bougies conservées en mémoire (défaut: 200).
    """

    def __init__(self, maxlen: int = 200):
        self._maxlen = maxlen
        self._buffer: Deque[Candle] = deque(maxlen=maxlen)
        self._lock = asyncio.Lock()
        self._last_update: float = 0.0
        self._updates_count: int = 0

    # ── Écriture (appelée par le listener WebSocket) ────────────────────

    async def upsert(self, candle: Candle) -> bool:
        """Ajoute ou met à jour une bougie dans le buffer.

        Si une bougie avec le même timestamp existe déjà (bougie en cours),
        elle est remplacée. Sinon, une nouvelle entrée est ajoutée.

        Returns
        -------
        True si la bougie était nouvelle (pas une mise à jour), False sinon.
        """
        async with self._lock:
            # Chercher si une bougie avec le même timestamp existe déjà
            for i, existing in enumerate(self._buffer):
                if existing.timestamp_ms == candle.timestamp_ms:
                    self._buffer[i] = candle
                    self._last_update = time.time()
                    self._updates_count += 1
                    return False  # mise à jour, pas nouvelle

            # Nouvelle bougie
            self._buffer.append(candle)
            self._last_update = time.time()
            self._updates_count += 1
            return True

    # ── Lecture (appelée par le SignalEngine / Orchestrator) ────────────

    def get_all(self) -> Tuple[Candle, ...]:
        """Retourne toutes les bougies sous forme de tuple immuable.

        Pas de lock car on lit une snapshot cohérente (deque est thread-safe
        en lecture seule, et les Candle sont immuables).
        """
        # Conversion snapshot : on fige la liste en tuple
        return tuple(self._buffer)

    def get_last(self, n: int = 1) -> Tuple[Candle, ...]:
        """Retourne les n dernières bougies.

        Parameters
        ----------
        n: int
            Nombre de bougies à retourner (les plus récentes).

        Returns
        -------
        Tuple de Candle (peut être vide si le buffer est vide).
        """
        all_candles = self.get_all()
        return all_candles[-n:] if len(all_candles) >= n else all_candles

    def get_last_closed(self) -> Optional[Candle]:
        """Retourne la dernière bougie fermée (ignore la bougie en cours).

        La bougie en cours a is_closed=False.  Le SignalEngine doit utiliser
        cette méthode pour éviter les biais de la bougie non fermée.
        """
        all_candles = list(self._buffer)
        # Parcourir à l'envers pour trouver la dernière bougie fermée
        for candle in reversed(all_candles):
            if candle.is_closed:
                return candle
        return None

    # ── Stats ───────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Nombre de bougies actuellement dans le buffer."""
        return len(self._buffer)

    @property
    def is_ready(self) -> bool:
        """True si le buffer contient assez de données pour tous les indicateurs."""
        return self.count >= 50  # 50 bougies = warmup pour Choppiness(14)+RSI(7)+ATR(10)

    @property
    def last_update(self) -> float:
        """Timestamp Unix de la dernière mise à jour."""
        return self._last_update

    @property
    def age_seconds(self) -> float:
        """Âge en secondes depuis la dernière mise à jour (> 30s = stale)."""
        if self._last_update == 0:
            return float("inf")
        return time.time() - self._last_update

    def clear(self) -> None:
        """Vide le buffer (utilisé avant un restart)."""
        self._buffer.clear()
        self._last_update = 0.0