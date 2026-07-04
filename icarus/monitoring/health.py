"""icarus.monitoring.health — Système de health checks global.

Interroge tous les modules (Data, Signal, Risk, Execution) via leur
méthode get_health() et agrège les statuts.  En cas de composant
UNHEALTHY, l'orchestrateur peut décider d'un arrêt d'urgence.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List

from icarus.core.interfaces import DataProvider, SignalEngine, RiskEngine, ExecutionEngine
from icarus.core.types import ComponentStatus, HealthStatus

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Moniteur de santé global.

    Parameters
    ----------
    data: DataProvider
    signal: SignalEngine
    risk: RiskEngine
    execution: ExecutionEngine
    critical_components: List[str]
        Noms des composants dont l'état UNHEALTHY déclenche un arrêt d'urgence.
    """

    def __init__(
        self,
        data: DataProvider,
        signal: SignalEngine,
        risk: RiskEngine,
        execution: ExecutionEngine,
        critical_components: List[str] | None = None,
    ):
        self._components: Dict[str, object] = {
            "DataProvider": data,
            "SignalEngine": signal,
            "RiskEngine": risk,
            "ExecutionEngine": execution,
        }
        self._critical = critical_components or ["DataProvider", "ExecutionEngine"]
        self._last_check = 0.0
        self._check_count = 0

    def check_all(self) -> List[HealthStatus]:
        """Interroge tous les composants et retourne leurs statuts.

        Returns
        -------
        Liste de HealthStatus, un par composant.
        """
        statuses: List[HealthStatus] = []
        self._check_count += 1
        self._last_check = time.time()

        for name, component in self._components.items():
            try:
                status = component.get_health()
                statuses.append(status)
            except Exception as e:
                logger.error(f"[HealthMonitor] Erreur lors du check de {name}: {e}")
                statuses.append(
                    HealthStatus(
                        component=name,
                        status=ComponentStatus.UNHEALTHY,
                        message=f"Erreur: {e}",
                        since=time.time(),
                    )
                )

        return statuses

    def is_healthy(self) -> bool:
        """True si tous les composants critiques sont HEALTHY ou DEGRADED.

        Un composant DEGRADED n'empêche pas le fonctionnement,
        mais un composant UNHEALTHY arrête le bot s'il est critique.
        """
        statuses = self.check_all()
        for s in statuses:
            if s.component in self._critical and s.status == ComponentStatus.UNHEALTHY:
                logger.critical(
                    f"[HealthMonitor] Composant critique UNHEALTHY: {s.component} — {s.message}"
                )
                return False
        return True

    def summary(self) -> str:
        """Retourne un résumé textuel de l'état de santé."""
        statuses = self.check_all()
        lines = ["HEALTH CHECK:"]
        for s in statuses:
            emoji = {"healthy": "✅", "degraded": "⚠️", "unhealthy": "🚨"}.get(
                s.status.value, "❓"
            )
            lines.append(f"  {emoji} {s.component:<25} {s.message}")
        return "\n".join(lines)

    @property
    def last_check_age(self) -> float:
        """Âge du dernier health check en secondes."""
        if self._last_check == 0:
            return float("inf")
        return time.time() - self._last_check