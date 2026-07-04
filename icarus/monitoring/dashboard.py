"""icarus.monitoring.dashboard — Dashboard console en temps réel.

Utilise des codes ANSI pour rafraîchir l'affichage sans dépendance externe.
Affiche l'état des modules, les stats de trading, et le health check.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import List, Optional

from icarus.core.types import (
    BotStats,
    ClosedTrade,
    ComponentStatus,
    HealthStatus,
    MicroStructure,
)

logger = logging.getLogger(__name__)


class Dashboard:
    """Dashboard console avec rafraîchissement ANSI.

    Affiche un tableau de bord compact :
      • Ligne 1 : Prix, Spread, Imbalance
      • Ligne 2 : Risk (PnL jour, drawdown, halt status)
      • Ligne 3 : Positions (actives, pending, signaux)
      • Ligne 4 : Performance (win rate, PnL total)
      • Ligne 5 : Dernier trade clôturé
      • Ligne 6 : Health status de chaque module
    """

    def __init__(self, symbol: str = "BTC/USDT", refresh_interval: float = 2.0):
        self._symbol = symbol
        self._refresh_interval = refresh_interval
        self._last_render = 0.0
        self._running = True

    # ── Rendu principal ─────────────────────────────────────────────────

    def render(
        self,
        micro: Optional[MicroStructure] = None,
        risk_stats: Optional[BotStats] = None,
        signals_generated: int = 0,
        active_positions: int = 0,
        pending_orders: int = 0,
        last_trade: Optional[ClosedTrade] = None,
        health_statuses: Optional[List[HealthStatus]] = None,
        cooldown_remaining: float = 0.0,
    ) -> None:
        """Affiche (ou rafraîchit) le dashboard dans la console.

        Parameters
        ----------
        micro: MicroStructure, optional
            Microstructure courante.
        risk_stats: BotStats, optional
            Statistiques du RiskEngine.
        signals_generated: int
            Nombre de signaux générés depuis le démarrage.
        active_positions: int
            Nombre de trades actifs.
        pending_orders: int
            Nombre d'ordres en attente.
        last_trade: ClosedTrade, optional
            Dernier trade clôturé.
        health_statuses: List[HealthStatus], optional
            Statuts de santé de tous les modules.
        cooldown_remaining: float
            Secondes restantes avant le prochain trade autorisé.
        """
        # Rafraîchissement périodique seulement
        now = time.time()
        if now - self._last_render < self._refresh_interval:
            return
        self._last_render = now

        # Clear screen (ANSI)
        print("\033[H\033[J", end="")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print("═" * 62)
        print(f"  🤖 ICARUS SCALPER 1M — {now_str}")
        print("═" * 62)

        # ── Ligne 1 : Prix et Microstructure ──
        if micro and micro.mid_price > 0:
            spread_pct = micro.spread * 100
            imbalance = micro.imbalance
            print(
                f"  📊 {self._symbol:<12} Mid: {micro.mid_price:>10.2f}  "
                f"Spread: {spread_pct:.4f}%  Imbalance: {imbalance:.2f}"
            )
        else:
            print(f"  📊 {self._symbol:<12} En attente de données...")

        # ── Ligne 2 : Risk ──
        if risk_stats:
            pnl_color = "+" if risk_stats.daily_pnl >= 0 else ""
            halt = "⛔ HALT" if risk_stats.daily_pnl < -1 else "✅ ACTIF"
            dd = risk_stats.max_drawdown * 100 if hasattr(risk_stats, "max_drawdown") else 0.0
            print(
                f"  🛡️  Risk: {halt}  |  PnL Jour: {pnl_color}{risk_stats.daily_pnl:.2f} $  |  "
                f"DD: {dd:.2f}%"
            )
        else:
            print("  🛡️  Risk: Initialisation...")

        # ── Ligne 3 : Positions ──
        cd_part = ""
        if cooldown_remaining > 0:
            cd_part = f"  |  Cooldown: {cooldown_remaining:.0f}s"
        print(
            f"  📈 Trades: {active_positions} actifs / {pending_orders} pending  "
            f"|  Signaux: {signals_generated}{cd_part}"
        )

        # ── Ligne 4 : Performance ──
        if risk_stats:
            total = risk_stats.win_count + risk_stats.loss_count
            wr = (risk_stats.win_count / total * 100) if total > 0 else 0
            print(
                f"  🏆 Win Rate: {wr:.1f}% ({risk_stats.win_count}W/{risk_stats.loss_count}L)  "
                f"|  PnL Total: {risk_stats.total_pnl:+.2f} $"
            )
        else:
            print("  🏆 Win Rate: --  |  PnL Total: --")

        # ── Ligne 5 : Dernier trade ──
        if last_trade:
            emoji = "🟢" if last_trade.is_winner else "🔴"
            print(
                f"  {emoji} Dernier trade: {last_trade.side.upper():<5} | "
                f"PnL: {last_trade.pnl:+.2f} $ | {last_trade.reason.value}"
            )
        else:
            print("  ℹ️  Aucun trade clôturé pour l'instant.")

        # ── Ligne 6 : Health ──
        print("  ── Health ──")
        if health_statuses:
            for s in health_statuses:
                emoji = {"healthy": "✅", "degraded": "⚠️", "unhealthy": "🚨"}.get(
                    s.status.value, "❓"
                )
                print(f"    {emoji} {s.component:<22} {s.message}")
        else:
            print("    ⏳ En attente du premier check...")

        print("═" * 62)
        print("  Ctrl+C pour arrêter proprement.")