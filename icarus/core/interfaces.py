"""icarus.core.interfaces — Contrats abstraits pour tous les modules.

Chaque module de l'application implémente l'une de ces interfaces.
Cela permet de :
  • Tester chaque module isolément avec des mocks.
  • Changer l'implémentation (ex: Binance → Bybit) sans toucher au reste du code.
  • Documenter clairement les responsabilités de chaque composant.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional

from icarus.core.types import (
    BotStats,
    ClosedTrade,
    HealthStatus,
    MarketSnapshot,
    MicroStructure,
    OrderRequest,
    PositionSize,
    TradingSignal,
    ValidatedSignal,
)


# ═══════════════════════════════════════════════════════════════════════════
# DATA PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

class DataProvider(ABC):
    """Contrat pour tout fournisseur de données temps réel (Binance, Bybit, …)."""

    @abstractmethod
    async def start(self) -> None:
        """Démarre les flux de données (WebSockets, buffer initial)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Arrête proprement les flux de données."""
        ...

    @abstractmethod
    def get_snapshot(self) -> MarketSnapshot:
        """Retourne un snapshot complet du marché (candles + micro).

        Appelé à chaque itération par le SignalEngine.  Si les données ne sont
        pas encore disponibles, lève ``DataNotReadyError``.
        """
        ...

    @abstractmethod
    def get_micro_structure(self) -> MicroStructure:
        """Retourne uniquement la microstructure (pour l'exécution rapide).

        Moins coûteux que ``get_snapshot()``, utilisé par l'ExecutionEngine
        pour vérifier les conditions de sortie.
        """
        ...

    @abstractmethod
    def get_health(self) -> HealthStatus:
        """Retourne le statut de santé du DataProvider."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True si au moins un flux WebSocket est actif."""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class SignalEngine(ABC):
    """Contrat pour le moteur de génération de signaux.

    Prend un ``MarketSnapshot`` en entrée, produit un ``TradingSignal``.
    """

    @abstractmethod
    def generate(self, snapshot: MarketSnapshot) -> TradingSignal:
        """Produit un signal de trading à partir d'un snapshot.

        Ne doit PAS lever d'exception — en cas d'erreur, retourne un
        ``TradingSignal(action=WAIT, reason="...")``.
        """
        ...

    @abstractmethod
    def get_health(self) -> HealthStatus:
        """Retourne le statut de santé du SignalEngine."""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# RISK ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class RiskEngine(ABC):
    """Contrat pour le contrôleur de risque.

    Valide les signaux, calcule la taille de position (Kelly),
    gère le circuit breaker et le suivi du PnL.
    """

    @abstractmethod
    def validate_and_size(
        self,
        signal: TradingSignal,
        current_balance: float,
        open_positions: int,
    ) -> ValidatedSignal:
        """Valide un signal et calcule la taille de position associée.

        Si le signal est rejeté (circuit breaker, max positions, Kelly nul),
        retourne ``ValidatedSignal(is_valid=False, reason="...")``.
        """
        ...

    @abstractmethod
    def check_circuit_breaker(self, current_balance: float) -> tuple[bool, str]:
        """Vérifie si un circuit breaker est actif.

        Returns
        -------
        (halted: bool, reason: str)
        """
        ...

    @abstractmethod
    def update_pnl(self, pnl: float, *, is_winner: Optional[bool] = None) -> None:
        """Met à jour le PnL courant (appelé après clôture d'un trade)."""
        ...

    @abstractmethod
    def record_trade(self, trade: ClosedTrade) -> None:
        """Enregistre un trade clôturé dans l'historique persistant."""
        ...

    @abstractmethod
    def get_stats(self) -> BotStats:
        """Retourne les statistiques de risque (PnL, win rate, drawdown)."""
        ...

    @abstractmethod
    def get_health(self) -> HealthStatus:
        """Retourne le statut de santé du RiskEngine."""
        ...

    @property
    @abstractmethod
    def max_positions(self) -> int:
        """Nombre maximum de positions simultanées autorisées."""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class ExecutionEngine(ABC):
    """Contrat pour le moteur d'exécution d'ordres.

    Gère le cycle de vie complet : placement → suivi → clôture (TP/SL/Trailing).
    """

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> bool:
        """Place un ordre d'entrée (limit ou market).

        Returns
        -------
        True si l'ordre a été accepté par l'exchange, False sinon.
        """
        ...

    @abstractmethod
    async def monitor_orders(self) -> None:
        """Vérifie le statut des ordres en attente et des trades actifs.

        À appeler régulièrement (toutes les ~3-5 secondes).
        Détecte les remplissages, timeouts, TP/SL/Trailing hits.
        """
        ...

    @abstractmethod
    async def get_closed_trades(self) -> List[ClosedTrade]:
        """Récupère et vide la file des trades clôturés depuis le dernier appel."""
        ...

    @abstractmethod
    async def get_open_positions_count(self) -> int:
        """Nombre de positions actuellement ouvertes."""
        ...

    @abstractmethod
    async def get_pending_orders_count(self) -> int:
        """Nombre d'ordres en attente de remplissage."""
        ...

    @abstractmethod
    async def get_balance(self, currency: str = "USDT") -> float:
        """Solde disponible pour la devise donnée."""
        ...

    @abstractmethod
    async def cancel_all(self) -> int:
        """Annule TOUS les ordres en attente.

        Returns
        -------
        Nombre d'ordres annulés.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Ferme la session de l'exchange proprement."""
        ...

    @abstractmethod
    def get_health(self) -> HealthStatus:
        """Retourne le statut de santé de l'ExecutionEngine."""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# ERRORS
# ═══════════════════════════════════════════════════════════════════════════

class DataNotReadyError(Exception):
    """Levée quand le DataProvider n'a pas encore assez de données."""
    pass


class ExchangeError(Exception):
    """Levée en cas d'erreur de communication avec l'exchange."""
    pass