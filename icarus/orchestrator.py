"""icarus.orchestrator — Cœur du bot de trading multi-paires.

Remplace l'ancien scaler_loop.py.  L'orchestrateur :
  • Initialise N DataProviders (un par paire) avec injection de dépendances
  • À chaque itération, scanne toutes les paires et garde le meilleur signal
  • Connecte les événements (pub/sub via EventBus)
  • Exécute une boucle de maintenance légère (health check, dashboard)
  • Délègue la logique métier aux modules (Signal/Risk/Execution)
  • Envoie les notifications Telegram (trades, santé, rapport quotidien)
  • Gère le graceful shutdown proprement

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import signal as signal_module
import time
from datetime import datetime
from typing import Dict, List, Optional

from icarus.config.loader import load_config
from icarus.config.models import BaseConfig
from icarus.core.events import EventBus, EventType
from icarus.core.interfaces import (
    DataProvider,
    ExecutionEngine,
    RiskEngine,
    SignalEngine,
)
from icarus.core.types import (
    ClosedTrade,
    ComponentStatus,
    MarketSnapshot,
    MicroStructure,
    OrderRequest,
    TradeAction,
    TradingSignal,
)
from icarus.data.stream import BinanceDataProvider
from icarus.execution import SpotExecutionController, FuturesExecutionController
from icarus.monitoring.dashboard import Dashboard
from icarus.monitoring.health import HealthMonitor
from icarus.monitoring.telegram import TelegramBot
from icarus.risk import SpotRiskController, FuturesRiskController
from icarus.signal.engine import ScalpingSignalEngine

logger = logging.getLogger("Orchestrator")

# Type pour un signal avec sa paire associée
SignalScore = tuple[str, TradingSignal, float]  # (symbol, signal, score)


class Orchestrator:
    """Orchestrateur principal du bot Icarus (multi-paires).

    Parameters
    ----------
    config_path: str
        Chemin vers le fichier de configuration YAML.
    """

    def __init__(self, config_path: str = "config.yaml"):
        # Chargement de la configuration
        self._raw_config: BaseConfig = load_config(config_path)
        self._cfg = self._raw_config.scalping
        self._exchange_cfg = self._raw_config.exchange
        self._telegram_cfg = self._raw_config.telegram

        # Bus d'événements global
        self._bus = EventBus()

        # Data providers (un par paire)
        self._data_providers: Dict[str, DataProvider] = {}

        # Modules uniques
        self._signal: Optional[SignalEngine] = None
        self._risk: Optional[RiskEngine] = None
        self._execution: Optional[ExecutionEngine] = None
        self._health: Optional[HealthMonitor] = None
        self._dashboard: Optional[Dashboard] = None

        # Telegram
        self._telegram: Optional[TelegramBot] = None

        # Flag de pause (pour commande /pause)
        self._paused = False

        # Flag anti-double shutdown
        self._shutting_down = False

        # Timestamps d'ouverture des positions (pour durée dans trade alert)
        self._position_timestamps: Dict[str, float] = {}

        # État
        self._running = False
        self._cooldown_until: float = 0.0
        self._signals_count = 0
        self._start_time = 0.0
        self._current_symbol: Optional[str] = None  # paire actuellement tradée

        # Dernières valeurs (pour dashboard/telegram)
        self._last_micros: Dict[str, MicroStructure] = {}
        self._last_scores: Dict[str, float] = {}
        self._last_trade: Optional[ClosedTrade] = None
        self._last_health_statuses: List = []

    # ═════════════════════════════════════════════════════════════════════
    # Initialisation
    # ═════════════════════════════════════════════════════════════════════

    async def _prepare_execution(self) -> None:
        """Prépare la session futures si l'on trade sur les marchés dérivés."""
        if self._exchange_cfg.futures and isinstance(self._execution, FuturesExecutionController):
            await self._execution.initialize_futures_account()

    def _init_components(self) -> None:
        """Instancie tous les modules avec injection de dépendances."""
        logger.info("Initialisation des composants...")

        symbols = self._cfg.symbols
        logger.info(f"  Paires configurées: {', '.join(symbols)}")

        # Data providers (un par paire)
        for sym in symbols:
            provider = BinanceDataProvider(
                symbol=sym,
                buffer_size=200,
                event_bus=self._bus,
                stale_timeout=self._raw_config.health.stale_data_critical,
                futures=self._exchange_cfg.futures,
                sandbox=self._exchange_cfg.sandbox,
            )
            self._data_providers[sym] = provider
            logger.info(f"    ✅ DataProvider: {sym}")

        # Signal (un seul, générique — la paire est dans le snapshot)
        self._signal = ScalpingSignalEngine(config=self._cfg)

        # Risk
        if self._exchange_cfg.futures:
            self._risk = FuturesRiskController(config=self._cfg, db_path="icarus_risk.db")
        else:
            self._risk = SpotRiskController(config=self._cfg, db_path="icarus_risk.db")

        # Execution
        if self._exchange_cfg.futures:
            self._execution = FuturesExecutionController(
                config=self._cfg,
                exchange_cfg=self._exchange_cfg,
            )
        else:
            self._execution = SpotExecutionController(
                config=self._cfg,
                exchange_cfg=self._exchange_cfg,
            )

        # Health (avec le premier data provider pour l'interface)
        # Note: le health monitor sera étendu pour vérifier tous les providers
        self._health = HealthMonitor(
            data=list(self._data_providers.values())[0],
            signal=self._signal,
            risk=self._risk,
            execution=self._execution,
        )

        # Dashboard
        self._dashboard = Dashboard(
            symbol=symbols[0],
            refresh_interval=2.0,
        )

        # Telegram
        self._telegram = TelegramBot(config=self._telegram_cfg)
        self._register_telegram_commands()

        logger.info("✅ Tous les composants sont prêts.")

    # ═════════════════════════════════════════════════════════════════════
    # Cycle de vie principal
    # ═════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """Point d'entrée principal. Exécute le cycle de vie complet."""
        # 1. Initialisation
        self._init_components()
        await self._prepare_execution()
        self._running = True
        self._start_time = time.time()

        # 2. Démarrage de tous les DataStreams (avec stagger pour éviter
        #    d'ouvrir 6 connexions WebSocket simultanées vers le même host
        #    demo, ce qui provoque des timeouts d'ouverture de handshake).
        for sym, provider in self._data_providers.items():
            await provider.start()
            await asyncio.sleep(0.5)  # stagger de 500ms entre chaque symbole
        logger.info("🚀 Tous les flux de données sont démarrés.")

        # 3. Attente des premières données sur au moins une paire
        logger.info(f"⏳ Attente des premières données (50+ bougies 1m)...")
        await self._wait_for_data()

        logger.info("✅ Données suffisantes. Lancement de la boucle principale.")

        # 4. Démarrage du bot Telegram (sender worker + poller)
        if self._telegram:
            await self._telegram.start()

        # 5. Notification startup Telegram
        self._notify_startup()

        # 6. Enregistrement du graceful shutdown
        self._register_signal_handlers()

        # 7. Boucle principale
        try:
            await self._main_loop()
        except asyncio.CancelledError:
            logger.info("Boucle principale annulée.")
        finally:
            await self._shutdown()

    # ═════════════════════════════════════════════════════════════════════
    # Boucle principale (polling multi-paires)
    # ═════════════════════════════════════════════════════════════════════

    async def _main_loop(self) -> None:
        """Boucle de coordination principale.

        À chaque itération :
        1. Récupérer les snapshots de TOUTES les paires
        2. Surveiller les ordres en attente et trades actifs
        3. Traiter les trades clôturés (+ alertes Telegram)
        4. Scanner toutes les paires, garder le meilleur signal
        5. Mettre à jour le dashboard
        6. Health check périodique
        7. Rapport quotidien Telegram
        """
        interval = self._cfg.execution_loop_seconds
        last_health_check = 0.0
        last_daily_check = 0.0

        while self._running:
            try:
                # ── 1. DATA : Snapshots de toutes les paires ────────────
                snapshots = self._get_all_snapshots()

                # Mise à jour du mid_price pour l'exécution (prendre une paire active)
                mid_price = 0.0
                for snap in snapshots.values():
                    if snap and snap.micro.mid_price > 0:
                        mid_price = snap.micro.mid_price
                        break

                self._update_last_micros(snapshots)

                # ── 2. EXECUTION : Surveiller ordres & trades ───────────
                if self._execution:
                    await self._execution._monitor_pending_orders()
                    if mid_price > 0:
                        await self._execution.update_with_price(mid_price)

                # ── 3. EXECUTION : Traiter les trades clôturés ──────────
                await self._process_closed_trades()

                # ── 4. SIGNAL : Scanner toutes les paires ───────────────
                await self._scan_and_trade(snapshots)

                # ── 5. DASHBOARD ───────────────────────────────────────
                self._render_dashboard()

                # ── 6. HEALTH CHECK ────────────────────────────────────
                now = time.time()
                if self._health and (now - last_health_check) > self._raw_config.health.interval_seconds:
                    self._last_health_statuses = self._health.check_all() or []
                    if not self._health.is_healthy():
                        # Notifier Telegram
                        if self._telegram:
                            unhealthy = [s for s in self._last_health_statuses if s.status.value != "healthy"]
                            if unhealthy:
                                self._telegram.send_health_alert(unhealthy)

                        logger.error("[Orchestrator] ⛔ Health check FAILED — arrêt d'urgence.")
                        self._running = False
                        break
                    last_health_check = now

                # ── 7. RAPPORT QUOTIDIEN Telegram ───────────────────────
                if self._telegram and (now - last_daily_check) > 3600:  # vérifie toutes les heures
                    hour = time.localtime().tm_hour
                    if hour == self._telegram_cfg.daily_report_hour:
                        balance = 0.0
                        try:
                            balance = await self._execution.get_balance("USDT")
                        except Exception:
                            pass
                        self._telegram.send_daily_report(
                            self._risk.get_stats(), balance
                        )
                    last_daily_check = now

                # ── END ITERATION ──────────────────────────────────────
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[Orchestrator] Erreur dans la boucle principale: {e}")
                await asyncio.sleep(5)

    # ═════════════════════════════════════════════════════════════════════
    # Snapshots multi-paires
    # ═════════════════════════════════════════════════════════════════════

    def _get_all_snapshots(self) -> Dict[str, Optional[MarketSnapshot]]:
        """Récupère les snapshots de toutes les paires."""
        results: Dict[str, Optional[MarketSnapshot]] = {}
        for sym, provider in self._data_providers.items():
            try:
                results[sym] = provider.get_snapshot()
            except Exception as e:
                logger.debug(f"[Orchestrator] Snapshot {sym} indisponible: {e}")
                results[sym] = None
        return results

    def _update_last_micros(self, snapshots: Dict[str, Optional[MarketSnapshot]]) -> None:
        """Met à jour les dernières microstructures connues."""
        for sym, snap in snapshots.items():
            if snap and snap.micro.mid_price > 0:
                self._last_micros[sym] = snap.micro

    # ═════════════════════════════════════════════════════════════════════
    # Scanner multi-paires → meilleur signal
    # ═════════════════════════════════════════════════════════════════════

    async def _scan_and_trade(self, snapshots: Dict[str, Optional[MarketSnapshot]]) -> None:
        """Scanne toutes les paires, classe par score décroissant, trade le meilleur.

        Parameters
        ----------
        snapshots: dict
            Symbol → MarketSnapshot (ou None si indisponible).
        """
        # Garde-fous
        if self._paused:
            return

        if time.time() < self._cooldown_until:
            return

        pending = await self._execution.get_pending_orders_count()
        if pending > 0:
            return

        active = await self._execution.get_open_positions_count()
        if active >= self._risk.max_positions:
            return

        # Circuit breaker
        try:
            balance = await self._execution.get_balance("USDT")
        except Exception:
            logger.debug("[Orchestrator] Impossible de récupérer le solde.")
            return

        halted, reason = self._risk.check_circuit_breaker(balance)
        if halted:
            logger.debug(f"[Orchestrator] Circuit breaker: {reason}")
            return

        # Scanner toutes les paires et récolter les scores
        candidates: List[SignalScore] = []

        for sym, snap in snapshots.items():
            if snap is None:
                continue

            try:
                signal = self._signal.generate(snap)
            except Exception as e:
                logger.debug(f"[Orchestrator] Signal {sym} erreur: {e}")
                continue

            if signal.action == TradeAction.WAIT:
                continue

            # On garde le score brut (déjà dans le signal)
            candidates.append((sym, signal, signal.score))
            self._last_scores[sym] = signal.score

        if not candidates:
            self._signals_count += 0  # ne compte pas les WAIT
            return

        # Trier par score décroissant
        candidates.sort(key=lambda x: x[2], reverse=True)
        best_sym, best_signal, best_score = candidates[0]

        logger.info(
            f"[Orchestrator] 📊 Classement: "
            + " | ".join(f"{s}: {sc:.1f}" for s, _, sc in candidates[:3])
        )

        # Validation par le RiskEngine
        validated = self._risk.validate_and_size(best_signal, balance, active)
        self._signals_count += 1

        if not validated.is_valid:
            logger.debug(f"[Orchestrator] Signal {best_sym} rejeté: {validated.reason}")
            return

        # Exécution
        client_id = self._execution.generate_client_id()
        order = OrderRequest(
            client_id=client_id,
            symbol=best_sym,
            side="buy" if best_signal.action == TradeAction.BUY else "sell",
            order_type="limit",
            amount=validated.position.amount,
            price=best_signal.entry,
            sl=best_signal.sl,
            tp1=best_signal.tp1,
            tp2=best_signal.tp2,
            tp1_fraction=best_signal.tp1_fraction,
        )

        success = await self._execution.place_order(order)
        if success:
            self._cooldown_until = time.time() + self._cfg.cooldown_seconds
            self._current_symbol = best_sym

            # Enregistrer le timestamp pour calculer la durée du trade
            self._position_timestamps[client_id] = time.time()

            logger.info(
                f"[Orchestrator] ✅ ORDRE PLACÉ [{best_sym}]: {best_signal.action.value} "
                f"{validated.position.amount} @ {best_signal.entry}"
            )

            # Notification Telegram d'ouverture de position (non-bloquante)
            if self._telegram:
                direction_str = (
                    best_signal.direction.value
                    if best_signal.direction
                    else best_signal.action.value.upper()
                )
                self._telegram.send_position_open_alert(
                    symbol=best_sym,
                    direction=direction_str,
                    signal=best_signal,
                    position=validated.position,
                    leverage=self._cfg.leverage,
                )
        else:
            logger.warning("[Orchestrator] Échec du placement d'ordre.")

    # ═════════════════════════════════════════════════════════════════════
    # Trades clôturés
    # ═════════════════════════════════════════════════════════════════════

    async def _process_closed_trades(self) -> None:
        """Récupère les trades clôturés, met à jour le RiskEngine et notifie Telegram."""
        closed = await self._execution.get_closed_trades()
        for trade in closed:
            self._risk.record_trade(trade)
            self._last_trade = trade
            logger.info(
                f"[Orchestrator] Trade clôturé: {trade.client_id} [{trade.symbol}] | "
                f"PnL: {trade.pnl:+.2f} $ | {trade.reason.value}"
            )

            # Notification Telegram (non-bloquante, avec durée si dispo)
            if self._telegram:
                if trade.client_id in self._position_timestamps:
                    object.__setattr__(trade, '_opened_at', self._position_timestamps.pop(trade.client_id))
                self._telegram.send_trade_alert(trade)

    # ═════════════════════════════════════════════════════════════════════
    # Dashboard (enrichi multi-paires)
    # ═════════════════════════════════════════════════════════════════════

    def _render_dashboard(self) -> None:
        """Met à jour le dashboard avec les données multi-paires."""
        if not self._dashboard:
            return

        cooldown = max(0.0, self._cooldown_until - time.time())
        active = 0
        pending = 0
        if self._execution:
            try:
                # On ne peut pas await ici car le dashboard tourne pas en async,
                # mais get_open_positions_count utilise un lock synchrone
                pass  # sera récupéré en appelant directement
            except Exception:
                pass

        # Choisir la microstructure de la paire active ou la première dispo
        primary_micro = None
        if self._current_symbol and self._current_symbol in self._last_micros:
            primary_micro = self._last_micros[self._current_symbol]
        elif self._last_micros:
            primary_micro = next(iter(self._last_micros.values()))

        risk_stats = self._risk.get_stats() if self._risk else None

        # Enrichir le dashboard avec les scores par paire
        scores_summary = ", ".join(
            f"{sym}: {score:.0f}" for sym, score in sorted(
                self._last_scores.items(), key=lambda x: x[1], reverse=True
            )[:5]
        )

        self._dashboard.render(
            micro=primary_micro,
            risk_stats=risk_stats,
            signals_generated=self._signals_count,
            active_positions=active,
            pending_orders=pending,
            last_trade=self._last_trade,
            health_statuses=self._last_health_statuses if self._last_health_statuses else None,
            cooldown_remaining=cooldown,
        )

        # Afficher en plus les scores
        if scores_summary:
            print(f"\033[38;5;240m  📊 Scores: {scores_summary}\033[0m")

    # ═════════════════════════════════════════════════════════════════════
    # Wait for data
    # ═════════════════════════════════════════════════════════════════════

    async def _wait_for_data(self) -> None:
        """Attend qu'au moins une paire ait assez de données."""
        while self._running:
            for sym, provider in self._data_providers.items():
                if provider.buffer.count >= 50:
                    logger.info(f"  ✅ {sym}: {provider.buffer.count} bougies prêtes.")
                    return
            await asyncio.sleep(2)
            counts = {s: p.buffer.count for s, p in self._data_providers.items()}
            logger.debug(f"  Buffers: {counts}")

    # ═════════════════════════════════════════════════════════════════════
    # Telegram
    # ═════════════════════════════════════════════════════════════════════

    def _notify_startup(self) -> None:
        """Envoie une notification Telegram au démarrage."""
        if not self._telegram:
            return

        symbols_str = ", ".join(self._cfg.symbols)
        summary = (
            f"📋 <b>Configuration</b>\n"
            f"Paires: {symbols_str}\n"
            f"TP1: {self._cfg.tp1_percent*100:.1f}% | TP2: {self._cfg.tp2_percent*100:.1f}%\n"
            f"SL: {self._cfg.fixed_sl_percent*100:.1f}% | Cooldown: {self._cfg.cooldown_seconds}s\n"
            f"Risque/trade: {self._cfg.risk_per_trade*100:.1f}% | Kelly: {self._cfg.kelly_fraction*100:.0f}%\n"
            f"Seuil score: {self._cfg.threshold_base:.0f}"
        )
        self._telegram.send_startup(summary)

    # ═════════════════════════════════════════════════════════════════════
    # Commandes Telegram
    # ═════════════════════════════════════════════════════════════════════

    def _register_telegram_commands(self) -> None:
        """Enregistre les handlers de commandes Telegram."""
        if not self._telegram or not self._telegram.is_available:
            return

        self._telegram.register_command("start", self._cmd_start)
        self._telegram.register_command("help", self._cmd_help)
        self._telegram.register_command("status", self._cmd_status)
        self._telegram.register_command("pnl", self._cmd_pnl)
        self._telegram.register_command("positions", self._cmd_positions)
        self._telegram.register_command("config", self._cmd_config)
        self._telegram.register_command("pause", self._cmd_pause)
        self._telegram.register_command("resume", self._cmd_resume)
        self._telegram.register_command("stop", self._cmd_stop)

    # ── Handlers de commandes ───────────────────────────────────────────

    async def _cmd_help_list(self) -> str:
        """Retourne la liste des commandes (utilisée par /start et /help)."""
        return (
            "/status — État actuel du bot (PnL, positions, uptime)\n"
            "/pnl — Détail des performances\n"
            "/positions — Positions ouvertes\n"
            "/config — Configuration active\n"
            "/pause — Mettre en pause les nouveaux signaux\n"
            "/resume — Reprendre le scan\n"
            "/stop — Arrêter le bot\n"
            "/help — Afficher cette liste"
        )

    async def _cmd_start(self, chat_id: str, args: str) -> str:
        """Message de bienvenue + liste des commandes."""
        cmd_list = await self._cmd_help_list()
        return (
            "🚀 <b>Icarus Bot — Scalping Intraday Automatisé</b>\n\n"
            "Bot de trading multi-paires sur Binance Futures (USDS-M). "
            "Scalping algorithmique avec scoring multi-indicateurs, "
            "gestion de risque Kelly, et exécution automatique.\n\n"
            "📋 <b>Commandes disponibles :</b>\n"
            f"{cmd_list}\n\n"
            "<i>Envoyez une commande pour interagir avec le bot.</i>"
        )

    async def _cmd_help(self, chat_id: str, args: str) -> str:
        """Rappel des commandes."""
        cmd_list = await self._cmd_help_list()
        return f"📋 <b>Commandes disponibles :</b>\n{cmd_list}"

    async def _cmd_status(self, chat_id: str, args: str) -> str:
        """État global du bot."""
        uptime = time.time() - self._start_time
        hours, remainder = divmod(int(uptime), 3600)
        mins, secs = divmod(remainder, 60)
        uptime_str = f"{hours}h{mins:02d}m{secs:02d}s"

        active_positions = 0
        pending_orders = 0
        balance = 0.0
        if self._execution:
            try:
                active_positions = await self._execution.get_open_positions_count()
                pending_orders = await self._execution.get_pending_orders_count()
                balance = await self._execution.get_balance("USDT")
            except Exception:
                pass

        paused_str = "⏸️ PAUSÉ" if self._paused else "▶️ ACTIF"

        stats = self._risk.get_stats() if self._risk else None
        pnl_str = ""
        if stats:
            pnl_str = (
                f"PnL Jour: <b>{stats.daily_pnl:+.2f} $</b>\n"
                f"PnL Total: {stats.total_pnl:+.2f} $\n"
            )

        return (
            f"📊 <b>Statut Icarus</b> — {paused_str}\n\n"
            f"⏱️ Uptime: {uptime_str}\n"
            f"🏦 Solde: {balance:.2f} USDT\n"
            f"{pnl_str}"
            f"📈 Positions: {active_positions}\n"
            f"📋 Ordres en attente: {pending_orders}\n"
            f"🔔 Signaux générés: {self._signals_count}\n"
            f"⏳ Cooldown: {max(0, self._cooldown_until - time.time()):.0f}s\n\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )

    async def _cmd_pnl(self, chat_id: str, args: str) -> str:
        """Détail des performances."""
        stats = self._risk.get_stats() if self._risk else None
        if not stats:
            return "Aucune statistique disponible."

        total = stats.win_count + stats.loss_count
        wr = (stats.win_count / total * 100) if total > 0 else 0.0

        return (
            f"📊 <b>Performances</b>\n\n"
            f"💰 PnL Jour: <b>{stats.daily_pnl:+.2f} $</b>\n"
            f"💰 PnL Total: <b>{stats.total_pnl:+.2f} $</b>\n"
            f"\n"
            f"📈 Trades: {total} ({stats.win_count}W / {stats.loss_count}L)\n"
            f"🎯 Win Rate: {wr:.1f}%\n"
            f"📉 Max Drawdown: {stats.max_drawdown:.2f} $\n"
            f"\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )

    async def _cmd_positions(self, chat_id: str, args: str) -> str:
        """Liste des positions ouvertes."""
        if not self._execution:
            return "ExecutionEngine non initialisé."

        try:
            positions = await self._execution.get_open_positions()
        except Exception as e:
            return f"❌ Erreur récupération positions: {e}"

        if not positions:
            return "📭 Aucune position ouverte."

        lines = ["📈 <b>Positions ouvertes</b>\n"]
        for pos in positions:
            symbol = getattr(pos, 'symbol', '?')
            side = getattr(pos, 'side', '?')
            entry = getattr(pos, 'entry_price', 0.0)
            amount = getattr(pos, 'amount', 0.0)
            pnl = getattr(pos, 'unrealized_pnl', 0.0)
            pnl_pct = getattr(pos, 'unrealized_pnl_percent', 0.0)
            lines.append(
                f"{'📈' if str(side).upper() == 'BUY' else '📉'} <b>{symbol}</b> {str(side).upper()}\n"
                f"   Entrée: {entry:.4f} | Qté: {amount:.4f}\n"
                f"   PnL latent: {pnl:+.4f} $ ({pnl_pct:+.2f}%)\n"
            )

        lines.append(f"\n<i>{datetime.now().strftime('%H:%M:%S')}</i>")
        return "\n".join(lines)

    async def _cmd_config(self, chat_id: str, args: str) -> str:
        """Résumé de la configuration."""
        return (
            f"📋 <b>Configuration active</b>\n\n"
            f"Paires: {', '.join(self._cfg.symbols)}\n"
            f"Levier: {self._cfg.leverage}x | Mode: {self._cfg.margin_mode}\n"
            f"TP1: {self._cfg.tp1_percent*100:.1f}% (frac: {int(self._cfg.tp1_fraction*100)}%)\n"
            f"TP2: {self._cfg.tp2_percent*100:.1f}%\n"
            f"SL: {self._cfg.fixed_sl_percent*100:.1f}%\n"
            f"Risk/trade: {self._cfg.risk_per_trade*100:.2f}%\n"
            f"Kelly fraction: {self._cfg.kelly_fraction*100:.0f}%\n"
            f"Seuil score: {self._cfg.threshold_base:.0f}/100\n"
            f"Cooldown: {self._cfg.cooldown_seconds}s\n"
            f"Max positions: {self._cfg.max_positions}\n"
            f"\n<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )

    async def _cmd_pause(self, chat_id: str, args: str) -> str:
        """Met en pause le scan de nouveaux signaux."""
        if self._paused:
            return "⏸️ Le bot est déjà en pause."
        self._paused = True
        logger.info("[Telegram] /pause — Scan de signaux mis en pause.")
        return "⏸️ <b>Scan de signaux mis en PAUSE.</b>\nLes positions ouvertes continuent d'être gérées.\n\nUtilisez /resume pour reprendre."

    async def _cmd_resume(self, chat_id: str, args: str) -> str:
        """Reprend le scan de signaux."""
        if not self._paused:
            return "▶️ Le bot n'est pas en pause."
        self._paused = False
        logger.info("[Telegram] /resume — Scan de signaux repris.")
        return "▶️ <b>Scan de signaux REPRIS.</b>"

    async def _cmd_stop(self, chat_id: str, args: str) -> str:
        """Arrêt du bot."""
        logger.warning("[Telegram] /stop — Arrêt demandé via Telegram.")
        asyncio.create_task(self._shutdown_with_reason("Commande /stop Telegram"))
        return "🛑 <b>Arrêt du bot demandé.</b>\nFermeture en cours…"

    # ═════════════════════════════════════════════════════════════════════
    # Shutdown
    # ═════════════════════════════════════════════════════════════════════

    def _register_signal_handlers(self) -> None:
        """Enregistre les handlers pour SIGINT et SIGTERM."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal_module.SIGINT, signal_module.SIGTERM):
                try:
                    loop.add_signal_handler(
                        sig,
                        lambda s=sig: asyncio.create_task(self._shutdown()),
                    )
                except NotImplementedError:
                    pass  # Windows
        except Exception:
            logger.debug("Signal handlers non supportés sur cette plateforme.")

    async def _shutdown_with_reason(self, reason: str) -> None:
        """Arrêt propre avec raison personnalisée (appelé par /stop Telegram)."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info(f"🛑 Arrêt demandé: {reason}")
        # Le shutdown complet sera exécuté par _shutdown()
        await self._shutdown()

    async def _shutdown(self) -> None:
        """Arrêt propre de tous les modules."""
        if self._shutting_down:
            return
        self._shutting_down = True

        logger.info("🛑 Arrêt du bot en cours...")
        self._running = False

        # Annuler tous les ordres en attente
        if self._execution:
            try:
                cancelled = await self._execution.cancel_all()
                logger.info(f"  {cancelled} ordres annulés.")
            except Exception as e:
                logger.warning(f"  Erreur annulation ordres: {e}")

        # Arrêter tous les DataStreams
        for sym, provider in self._data_providers.items():
            try:
                await provider.stop()
                logger.info(f"  DataStream {sym} arrêté.")
            except Exception as e:
                logger.warning(f"  Erreur arrêt DataStream {sym}: {e}")

        # Fermer l'exchange
        if self._execution:
            try:
                await self._execution.close()
            except Exception as e:
                logger.warning(f"  Erreur fermeture exchange: {e}")

        # Arrêter le bot Telegram (sender worker + poller)
        if self._telegram:
            self._telegram.send_stop("Manuel (Ctrl+C)")
            await self._telegram.stop()

        logger.info("✅ Bot arrêté proprement.")