"""icarus.monitoring.telegram — Notifications Telegram asynchrones avec commandes interactives.

Fonctionnalités :
  • Queue asyncio non-bloquante — les send_*() push et retournent immédiatement
  • Retry avec exponential backoff (configurable)
  • Rate limiter (token bucket) pour respecter les 30 msg/sec de l'API Telegram
  • Commandes interactives via polling getUpdates (/status, /pause, /resume, /stop…)
  • Alertes d'ouverture et clôture de positions
  • 100% optionnel — si le bot n'est pas configuré (enabled=false), aucune requête

Les placeholders sont "TON_TOKEN_BOTFATHER" et "TON_CHAT_ID".
Si le token n'a pas été changé, les appels sont silently ignorés.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from icarus.config.models import TelegramConfig
from icarus.core.types import ClosedTrade, HealthStatus, BotStats, PositionSize, TradingSignal

logger = logging.getLogger(__name__)

# Petit check rapide pour aiohttp (facultatif)
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════
# Rate Limiter (token bucket)
# ═══════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Token bucket pour respecter le rate limit de l'API Telegram.

    Parameters
    ----------
    max_rate : float
        Nombre maximum de messages par seconde (défaut 20).
    """

    def __init__(self, max_rate: float = 20.0):
        self._tokens = max_rate
        self._max_tokens = max_rate
        self._last_refill = time.monotonic()
        self._refill_rate = max_rate

    async def acquire(self) -> None:
        """Bloque jusqu'à ce qu'un token soit disponible."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now
        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) / self._refill_rate
            await asyncio.sleep(wait)
            self._tokens = 0.0
        else:
            self._tokens -= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Telegram Bot
# ═══════════════════════════════════════════════════════════════════════════

class TelegramBot:
    """Bot Telegram asynchrone avec file d'envoi, retry, rate limiting et commandes.

    Parameters
    ----------
    config : TelegramConfig
        Configuration Telegram (token, chat_id, flags).
    """

    def __init__(self, config: TelegramConfig):
        self._cfg = config
        self._session: Optional[aiohttp.ClientSession] = None

        # File d'envoi non-bloquante
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._sender_task: Optional[asyncio.Task] = None

        # Rate limiter
        self._rate_limiter = RateLimiter(max_rate=config.rate_limit_max)

        # Commandes interactives
        self._handlers: Dict[str, Callable] = {}
        self._poller_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0
        self._polling = False

        # État
        self._last_report_sent: Optional[str] = None

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
            return False
        return True

    @property
    def is_admin(self, chat_id: str) -> bool:
        """Vérifie si un chat_id est autorisé (admin)."""
        allowed = {self._cfg.chat_id}
        allowed.update(self._cfg.admin_chat_ids)
        return str(chat_id) in allowed

    @property
    def _api_url(self) -> str:
        return f"https://api.telegram.org/bot{self._cfg.bot_token}/sendMessage"

    @property
    def _updates_url(self) -> str:
        return f"https://api.telegram.org/bot{self._cfg.bot_token}/getUpdates"

    # ── Cycle de vie ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Démarre le sender worker et le poller de commandes."""
        if not self.is_available:
            return

        await self._ensure_session()

        # Démarrer le worker d'envoi
        self._sender_task = asyncio.create_task(self._sender_worker())
        logger.info("[Telegram] Sender worker démarré.")

        # Démarrer le poller de commandes si activé
        if self._cfg.command_enabled:
            self._polling = True
            self._poller_task = asyncio.create_task(self._command_poller())
            logger.info("[Telegram] Command poller démarré.")

    async def stop(self) -> None:
        """Arrête proprement le sender worker, le poller et la session HTTP."""
        self._polling = False

        # Attendre que la queue se vide (avec timeout)
        if self._sender_task:
            try:
                await asyncio.wait_for(self._send_queue.join(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("[Telegram] Queue non vidée après 10s, abandon des messages restants.")
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
            self._sender_task = None

        if self._poller_task:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
            self._poller_task = None

        if self._session:
            await self._session.close()
            self._session = None
            logger.info("[Telegram] Session fermée.")

    async def _ensure_session(self) -> None:
        if self._session is None and AIOHTTP_AVAILABLE:
            self._session = aiohttp.ClientSession()

    # ── Sender Worker (queue consumer) ──────────────────────────────────

    async def _sender_worker(self) -> None:
        """Consumer : dépile les messages et les envoie avec retry + rate limit."""
        while True:
            try:
                text, parse_mode = await self._send_queue.get()
                await self._send_with_retry(text, parse_mode)
                self._send_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Telegram] Erreur sender worker: {e}")

    async def _send_with_retry(self, text: str, parse_mode: str = "HTML") -> bool:
        """Envoie un message avec retry et exponential backoff.

        Returns
        -------
        True si envoyé, False après épuisement des tentatives.
        """
        max_attempts = self._cfg.retry_max_attempts if self._cfg.retry_max_attempts > 0 else 1
        for attempt in range(max_attempts):
            if attempt > 0:
                delay = min(
                    self._cfg.retry_base_delay * (2.0 ** (attempt - 1)),
                    self._cfg.retry_max_delay,
                )
                logger.warning(f"[Telegram] Retry {attempt+1}/{max_attempts} dans {delay:.1f}s…")
                await asyncio.sleep(delay)

            success = await self._do_send(text, parse_mode)
            if success:
                return True

        logger.error(f"[Telegram] Échec définitif après {max_attempts} tentatives.")
        return False

    async def _do_send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Envoi HTTP unique (avec rate limit).

        Returns
        -------
        True si HTTP 200, False sinon.
        """
        if not self.is_available:
            return False

        try:
            await self._ensure_session()
            await self._rate_limiter.acquire()

            payload = {
                "chat_id": self._cfg.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            async with self._session.post(
                self._api_url, json=payload, timeout=aiohttp.ClientTimeout(10)
            ) as resp:
                if resp.status == 200:
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"[Telegram] Échec envoi (HTTP {resp.status}): {body[:200]}")
                    return False
        except Exception as e:
            logger.warning(f"[Telegram] Erreur envoi: {e}")
            return False

    # ── Méthodes publiques (push dans la queue, non-bloquantes) ─────────

    def _enqueue(self, text: str, parse_mode: str = "HTML") -> None:
        """Pousse un message dans la queue. Silencieux si non dispo."""
        if not self.is_available:
            return
        try:
            self._send_queue.put_nowait((text, parse_mode))
        except asyncio.QueueFull:
            logger.warning("[Telegram] Queue pleine — message ignoré.")

    # ────────────────────────────────────────────────────────────────────
    # Notifications automatiques (push)
    # ────────────────────────────────────────────────────────────────────

    def send_trade_alert(self, trade: ClosedTrade) -> None:
        """🔔 Notifie un trade clôturé (non-bloquant).

        Parameters
        ----------
        trade : ClosedTrade
            Trade clôturé à notifier.
        """
        if not self._cfg.notify_on_trade:
            return

        emoji = "🟢" if trade.is_winner else "🔴"
        side_emoji = "📈" if trade.side.upper() == "BUY" else "📉"
        duration_str = ""
        if hasattr(trade, '_opened_at') and trade._opened_at:
            duration_sec = trade.timestamp - trade._opened_at
            if duration_sec > 0:
                mins, secs = divmod(int(duration_sec), 60)
                duration_str = f"\n⏱️ Durée: {mins}m{secs:02d}s"

        text = (
            f"{emoji} <b>Trade clôturé</b>\n\n"
            f"{side_emoji} <b>{trade.symbol}</b> — {trade.side.upper()}\n"
            f"Entrée: <code>{trade.entry:.4f}</code>\n"
            f"Sortie: <code>{trade.exit:.4f}</code>\n"
            f"PnL: <b>{trade.pnl:+.4f} $</b> ({trade.pnl_percent:+.2f}%)"
            f"{duration_str}\n"
            f"Raison: {trade.reason.value}\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )

        self._enqueue(text)
        logger.info(f"[Telegram] Trade alert enqueued: {trade.pnl:+.4f}$")

    def send_position_open_alert(
        self,
        symbol: str,
        direction: str,
        signal: TradingSignal,
        position: PositionSize,
        leverage: int = 1,
    ) -> None:
        """🔔 Notifie l'ouverture d'une nouvelle position (non-bloquant).

        Parameters
        ----------
        symbol : str
            Paire tradée (ex: SOL/USDT).
        direction : str
            "LONG" ou "SHORT".
        signal : TradingSignal
            Signal qui a déclenché l'entrée.
        position : PositionSize
            Taille de position validée.
        leverage : int
            Levier utilisé (futures uniquement).
        """
        if not self._cfg.notify_on_position_open:
            return

        side_emoji = "📈" if direction.upper() == "LONG" else "📉"
        entry = signal.entry
        sl = signal.sl
        tp1 = signal.tp1
        tp2 = signal.tp2

        # Calcul des distances en %
        sl_pct = abs(entry - sl) / entry * 100
        tp1_pct = abs(tp1 - entry) / entry * 100
        tp2_pct = abs(tp2 - entry) / entry * 100

        lev_str = f"\n⚡ Levier: {leverage}x" if leverage > 1 else ""

        text = (
            f"🔔 <b>NOUVELLE POSITION</b>\n\n"
            f"{side_emoji} <b>{symbol}</b> — {direction.upper()}\n"
            f"💰 Entrée: <code>{entry:.4f}</code>\n"
            f"🛑 SL: <code>{sl:.4f}</code> (-{sl_pct:.2f}%)\n"
            f"🎯 TP1: <code>{tp1:.4f}</code> (+{tp1_pct:.2f}%) [{int(signal.tp1_fraction*100)}%]\n"
            f"🎯 TP2: <code>{tp2:.4f}</code> (+{tp2_pct:.2f}%) [{int((1-signal.tp1_fraction)*100)}%]\n\n"
            f"📊 Taille: {position.amount:.4f} ({position.exposure_usd:.2f} USDT)\n"
            f"⚠️ Risque: {position.risk_usd:.4f} USDT ({position.risk_pct:.2f}% du capital)"
            f"{lev_str}\n"
            f"⭐ Score: {signal.score:.0f}/100\n\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )

        self._enqueue(text)
        logger.info(f"[Telegram] Position open alert enqueued: {symbol} {direction}")

    def send_health_alert(self, statuses: List[HealthStatus]) -> None:
        """🏥 Notifie un problème de santé (non-bloquant).

        Parameters
        ----------
        statuses : List[HealthStatus]
            Statuts de tous les composants (seuls les unhealthy sont notifiés).
        """
        if not self._cfg.notify_on_health:
            return

        unhealthy = [s for s in statuses if s.status.value != "healthy"]
        if not unhealthy:
            return

        lines = ["🚨 <b>ALERTE SANTÉ</b>\n"]
        for s in unhealthy:
            emoji = {"degraded": "⚠️", "unhealthy": "🚨"}.get(s.status.value, "❓")
            lines.append(f"{emoji} <b>{s.component}</b>: {s.message}")

        lines.append(f"\n<i>{datetime.now().strftime('%H:%M:%S')}</i>")

        self._enqueue("\n".join(lines))
        logger.warning("[Telegram] Health alert enqueued.")

    def send_daily_report(self, stats: BotStats, balance: float = 0.0) -> None:
        """📊 Rapport quotidien récapitulatif (non-bloquant).

        Parameters
        ----------
        stats : BotStats
            Statistiques du RiskEngine.
        balance : float
            Solde courant du compte.
        """
        if not self._cfg.daily_report:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_report_sent == today:
            return

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

        self._enqueue(text)
        self._last_report_sent = today
        logger.info("[Telegram] Rapport quotidien enqueued.")

    def send_startup(self, config_summary: str) -> None:
        """🚀 Message de démarrage du bot (non-bloquant).

        Parameters
        ----------
        config_summary : str
            Résumé de la configuration (symbols, risk, etc.).
        """
        text = (
            f"🚀 <b>ICARUS DÉMARRÉ</b>\n\n"
            f"{config_summary}\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        self._enqueue(text)

    def send_stop(self, reason: str = "Manuel") -> None:
        """🛑 Message d'arrêt du bot (non-bloquant).

        Parameters
        ----------
        reason : str
            Raison de l'arrêt.
        """
        text = (
            f"🛑 <b>ICARUS ARRÊTÉ</b>\n\n"
            f"Raison: {reason}\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        self._enqueue(text)
        logger.info("[Telegram] Notification d'arrêt enqueued.")

    # ═════════════════════════════════════════════════════════════════════
    # Commandes interactives (polling getUpdates)
    # ═════════════════════════════════════════════════════════════════════

    def register_command(self, command: str, handler: Callable) -> None:
        """Enregistre un handler pour une commande (ex: '/status').

        Parameters
        ----------
        command : str
            Nom de la commande (sans le '/').
        handler : Callable
            Fonction async prenant (chat_id: str, args: str) → str.
            Retourne le texte de la réponse.
        """
        self._handlers[command] = handler

    async def _command_poller(self) -> None:
        """Polling loop : récupère les updates et dispatch les commandes."""
        while self._polling:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._dispatch(update)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Telegram] Poller error: {e}")
            await asyncio.sleep(self._cfg.command_polling_interval)

    async def _get_updates(self) -> List[dict]:
        """Récupère les updates Telegram via getUpdates avec offset."""
        if not self.is_available:
            return []

        try:
            await self._ensure_session()
            params: dict = {"timeout": self._cfg.command_polling_interval, "limit": 10}
            if self._last_update_id > 0:
                params["offset"] = self._last_update_id + 1

            async with self._session.get(
                self._updates_url, params=params, timeout=aiohttp.ClientTimeout(15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        return data.get("result", [])
                return []
        except Exception:
            return []

    async def _dispatch(self, update: dict) -> None:
        """Analyse une update et exécute la commande correspondante."""
        message = update.get("message")
        if not message:
            return

        # Mettre à jour l'offset
        self._last_update_id = max(self._last_update_id, update.get("update_id", 0))

        text = message.get("text", "")
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Vérifier les droits admin
        if not self.is_admin(chat_id):
            logger.debug(f"[Telegram] Commande ignorée de chat_id={chat_id} (non admin).")
            return

        # Parser la commande
        if not text.startswith("/"):
            return  # Ignorer les messages non-commandes

        parts = text.strip().split(maxsplit=1)
        cmd = parts[0][1:].lower().split("@")[0]  # retirer le / et le @nom_du_bot
        args = parts[1] if len(parts) > 1 else ""

        handler = self._handlers.get(cmd)
        if handler:
            logger.info(f"[Telegram] Commande /{cmd} reçue de chat_id={chat_id}")
            try:
                response = await handler(chat_id, args)
                if response:
                    # Réponse directe (bypass la queue pour l'interactivité)
                    await self._do_send(response)
            except Exception as e:
                logger.error(f"[Telegram] Erreur handler /{cmd}: {e}")
                await self._do_send(f"❌ Erreur lors de l'exécution de /{cmd}.")
        else:
            logger.debug(f"[Telegram] Commande inconnue: /{cmd}")