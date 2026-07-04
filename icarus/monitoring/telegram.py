"""icarus.monitoring.telegram — Notifications Telegram asynchrones.

Utilise aiohttp pour envoyer des messages via l'API Telegram Bot.
100% optionnel — si le bot n'est pas configuré (enabled=false),
aucune requête n'est envoyée.

Les placeholders sont "TON_TOKEN_BOTFATHER" et "TON_CHAT_ID".
Si le token n'a pas été changé, les appels sont silently ignorés.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import List, Optional

from icarus.config.models import TelegramConfig
from icarus.core.types import ClosedTrade, HealthStatus, BotStats

logger = logging.getLogger(__name__)

# Petit check rapide pour aiohttp (facultatif)
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class TelegramNotifier:
    """Notificateur Telegram asynchrone.

    Parameters
    ----------
    config: TelegramConfig
        Configuration Telegram (token, chat_id, flags).
    """

    def __init__(self, config: TelegramConfig):
        self._cfg = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_report_sent: Optional[str] = None  # date ISO du dernier rapport quotidien

    # ── Propriétés ──────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True si Telegram est activé ET configuré ET aiohttp dispo."""
        if not self._cfg.enabled:
            return False
        if not AIOHTTP_AVAILABLE:
            logger.warning("[Telegram] aiohttp non installé — pip install aiohttp")
            return False
        if "TON_TOKEN" in self._cfg.bot_token or "TON_CHAT_ID" in self._cfg.chat_id:
            return False  # placeholder, pas de vrai token
        return True

    @property
    def _api_url(self) -> str:
        return f"https://api.telegram.org/bot{self._cfg.bot_token}/sendMessage"

    # ── Session ─────────────────────────────────────────────────────────

    async def _ensure_session(self) -> None:
        if self._session is None and AIOHTTP_AVAILABLE:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── Envoi générique ─────────────────────────────────────────────────

    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Envoie un message Telegram (silencieux si non configuré).

        Returns
        -------
        True si envoyé, False sinon.
        """
        if not self.is_available:
            return False

        try:
            await self._ensure_session()
            payload = {
                "chat_id": self._cfg.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            async with self._session.post(self._api_url, json=payload, timeout=aiohttp.ClientTimeout(10)) as resp:
                if resp.status == 200:
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"[Telegram] Échec envoi (HTTP {resp.status}): {body[:200]}")
                    return False
        except Exception as e:
            logger.warning(f"[Telegram] Erreur envoi: {e}")
            return False

    # ═══════════════════════════════════════════════════
    # Alertes spécifiques
    # ═══════════════════════════════════════════════════

    async def send_trade_alert(self, trade: ClosedTrade) -> bool:
        """🔔 Notifie un trade clôturé.

        Parameters
        ----------
        trade: ClosedTrade
            Trade clôturé à notifier.
        """
        if not self._cfg.notify_on_trade:
            return False

        emoji = "🟢" if trade.is_winner else "🔴"
        side_emoji = "📈" if trade.side.upper() == "BUY" else "📉"

        text = (
            f"{emoji} <b>Trade clôturé</b>\n\n"
            f"{side_emoji} <b>{trade.symbol}</b> — {trade.side.upper()}\n"
            f"Entrée: <code>{trade.entry:.4f}</code>\n"
            f"Sortie: <code>{trade.exit:.4f}</code>\n"
            f"PnL: <b>{trade.pnl:+.4f} $</b> ({trade.pnl_percent:+.2f}%)\n"
            f"Raison: {trade.reason.value}\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )

        success = await self._send(text)
        if success:
            logger.info(f"[Telegram] Trade alert envoyé: {trade.pnl:+.4f}$")
        return success

    async def send_health_alert(self, statuses: List[HealthStatus]) -> bool:
        """🏥 Notifie un problème de santé.

        Parameters
        ----------
        statuses: List[HealthStatus]
            Statuts de tous les composants (seuls les unhealthy sont notifiés).
        """
        if not self._cfg.notify_on_health:
            return False

        unhealthy = [s for s in statuses if s.status.value != "healthy"]
        if not unhealthy:
            return False  # pas d'alerte si tout va bien

        lines = ["🚨 <b>ALERTE SANTÉ</b>\n"]
        for s in unhealthy:
            emoji = {"degraded": "⚠️", "unhealthy": "🚨"}.get(s.status.value, "❓")
            lines.append(f"{emoji} <b>{s.component}</b>: {s.message}")

        lines.append(f"\n<i>{datetime.now().strftime('%H:%M:%S')}</i>")

        success = await self._send("\n".join(lines))
        if success:
            logger.warning("[Telegram] Health alert envoyée.")
        return success

    async def send_daily_report(self, stats: BotStats, balance: float = 0.0) -> bool:
        """📊 Rapport quotidien récapitulatif.

        Parameters
        ----------
        stats: BotStats
            Statistiques du RiskEngine.
        balance: float
            Solde courant du compte.
        """
        if not self._cfg.daily_report:
            return False

        # Éviter le double envoi le même jour
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_report_sent == today:
            return False

        total = stats.win_count + stats.loss_count
        wr = (stats.win_count / total * 100) if total > 0 else 0.0

        text = (
            f"📊 <b>Rapport Quotidien — {today}</b>\n\n"
            f"💰 PnL Jour: <b>{stats.daily_pnl:+.2f} $</b>\n"
            f"🏦 Solde: {balance:.2f} $\n"
            f"\n"
            f"📈 Trades: {total} ({stats.win_count}W / {stats.loss_count}L)\n"
            f"🎯 Win Rate: {wr:.1f}%\n"
            f"📊 PnL Total: {stats.total_pnl:+.2f} $\n"
            f"\n"
            f"<i>Généré par Icarus à {datetime.now().strftime('%H:%M')}</i>"
        )

        success = await self._send(text)
        if success:
            self._last_report_sent = today
            logger.info("[Telegram] Rapport quotidien envoyé.")
        return success

    async def send_startup(self, config_summary: str) -> bool:
        """🚀 Message de démarrage du bot.

        Parameters
        ----------
        config_summary: str
            Résumé de la configuration (symbols, risk, etc.).
        """
        text = (
            f"🚀 <b>ICARUS DÉMARRÉ</b>\n\n"
            f"{config_summary}\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        return await self._send(text)

    async def send_stop(self, reason: str = "Manuel") -> bool:
        """🛑 Message d'arrêt du bot.

        Parameters
        ----------
        reason: str
            Raison de l'arrêt.
        """
        text = (
            f"🛑 <b>ICARUS ARRÊTÉ</b>\n\n"
            f"Raison: {reason}\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        success = await self._send(text)
        if success:
            logger.info("[Telegram] Notification d'arrêt envoyée.")
        return success