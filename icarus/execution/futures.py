"""Moteur d'exécution futures spécifique pour le scalping."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from icarus.config.models import ExchangeConfig, ScalpingConfig
from icarus.core.types import OrderRequest
from icarus.execution.engine import SpotExecutionController

logger = logging.getLogger(__name__)


class FuturesExecutionController(SpotExecutionController):
    """Execution controller pour marchés futures."""

    def __init__(self, config: ScalpingConfig, exchange_cfg: ExchangeConfig):
        super().__init__(config=config, exchange_cfg=exchange_cfg)

    def _init_exchange(self) -> None:
        """Initialise la session CCXT en mode futures."""
        if self._exchange is None:
            exchange_name = self._exchange_cfg.exchange
            exchange_class = getattr(__import__("ccxt", fromlist=[exchange_name]), exchange_name)
            self._exchange = exchange_class({
                "apiKey": self._exchange_cfg.api_key,
                "secret": self._exchange_cfg.api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            })
            if self._exchange_cfg.sandbox:
                self._enable_demo_trading(exchange_name)
            logger.info(f"[FuturesExecutionController] Exchange {exchange_name} initialisé en futures.")

    def _enable_demo_trading(self, exchange_name: str) -> None:
        """Active le demo trading Binance Futures (demo-fapi.binance.com).

        Binance a déprécié le testnet public pour les futures.  Les clés API
        générées sur https://demo.binance.com fonctionnent avec les endpoints
        demo-fapi.binance.com / demo-dapi.binance.com.

        Plutôt que de mapper les clés du dict CCXT 'demo' (qui peut être
        incomplet ou varier entre versions), on fait une substitution par
        nom d'hôte sur toutes les URLs futures.  C'est plus robuste.
        """
        if exchange_name.lower() != "binance":
            try:
                self._exchange.set_sandbox_mode(True)
                logger.info(
                    f"[FuturesExecutionController] Mode sandbox activé pour {exchange_name}."
                )
            except Exception as exc:
                logger.warning(
                    f"[FuturesExecutionController] set_sandbox_mode() échoué "
                    f"pour {exchange_name}: {exc}"
                )
            return

        # Binance Futures : remplacer les hosts de production par les hosts demo
        substitutions = {
            "fapi.binance.com": "demo-fapi.binance.com",
            "dapi.binance.com": "demo-dapi.binance.com",
        }

        api_urls = self._exchange.urls.get("api", {})
        overridden = 0
        for key, url in list(api_urls.items()):
            for prod_host, demo_host in substitutions.items():
                if prod_host in url:
                    api_urls[key] = url.replace(prod_host, demo_host)
                    overridden += 1
                    break

        if overridden > 0:
            logger.info(
                "[FuturesExecutionController] Demo trading Binance activé "
                f"({overridden} endpoints redirigés vers demo-*.binance.com)."
            )
        else:
            logger.warning(
                "[FuturesExecutionController] Aucun endpoint Binance futures "
                "trouvé dans les URLs — le demo trading pourrait ne pas "
                "fonctionner.  Vérifiez votre version de CCXT."
            )

    async def initialize_futures_account(self) -> None:
        """Configure le levier et le mode de marge une fois l'exchange initialisé."""
        if self._exchange is None:
            return

        loop = asyncio.get_running_loop()
        for symbol in self._config.symbols:
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._exchange.set_margin_mode(self._config.margin_mode, symbol),
                )
                await loop.run_in_executor(
                    None,
                    lambda: self._exchange.set_leverage(self._config.leverage, symbol),
                )
                logger.info(
                    f"[FuturesExecutionController] {symbol}: leverage={self._config.leverage}, "
                    f"margin_mode={self._config.margin_mode}"
                )
            except Exception as exc:
                error_msg = str(exc)
                if "testnet" in error_msg.lower() or "sandbox" in error_msg.lower():
                    logger.warning(
                        f"[FuturesExecutionController] Le testnet/sandbox Binance futures est déprécié. "
                        f"Si vous utilisez Binance, passez en mode demo (consultez la doc). "
                        f"Erreur originale: {exc}"
                    )
                else:
                    logger.warning(
                        f"[FuturesExecutionController] Impossible de configurer le futures account: {exc}"
                    )

    def _build_order_params(self, order: OrderRequest) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if self._config.hedge_mode:
            params["positionSide"] = "LONG" if order.side.lower() == "buy" else "SHORT"
        return params

    async def _async_create_order(
        self, symbol: str, order_type: str, side: str,
        amount: float, price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Place un ordre futures en mode asynchrone."""
        loop = asyncio.get_running_loop()
        params = params or {}
        if order_type == "limit":
            return await loop.run_in_executor(
                None,
                lambda: self._exchange.create_limit_order(symbol, side, amount, price, params),
            )
        else:
            return await loop.run_in_executor(
                None,
                lambda: self._exchange.create_market_order(symbol, side, amount, params),
            )

    async def place_order(self, order: OrderRequest) -> bool:
        """Place un ordre futures en limit ou market."""
        symbol = self._config.symbol

        try:
            balance = await self.get_balance("USDT")
            cost = order.amount * order.price
            if cost > balance * 0.95:
                logger.error("[FuturesExecutionController] Solde futures insuffisant, ordre annulé.")
                return False
        except Exception:
            logger.warning("[FuturesExecutionController] Impossible de vérifier le solde futures, tentative quand même.")

        logger.info(
            f"[FuturesExecutionController] Tentative {order.side.upper()} | {order.amount} @ {order.price} | "
            f"SL:{order.sl} | TP1:{order.tp1} | TP2:{order.tp2}"
        )

        try:
            params = self._build_order_params(order)
            result = await self._async_create_order(
                symbol=symbol,
                order_type=order.order_type,
                side=order.side,
                amount=order.amount,
                price=order.price if order.order_type == "limit" else None,
                params=params,
            )
        except Exception as e:
            logger.error(f"[FuturesExecutionController] Échec placement: {e}")
            return False

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
