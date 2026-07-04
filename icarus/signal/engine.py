"""icarus.signal.engine — Implémentation concrète de l'interface SignalEngine.

Combine les indicateurs (indicators.py) et le scoring (scoring.py)
pour produire un TradingSignal à partir d'un MarketSnapshot.

Ne fait AUCUN appel réseau — toutes les données sont déjà dans le snapshot.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import numpy as np

from icarus.config.models import ScalpingConfig
from icarus.core.interfaces import SignalEngine as SignalEngineInterface
from icarus.core.types import (
    Candle,
    ComponentStatus,
    Direction,
    HealthStatus,
    IndicatorSuite,
    MarketRegime,
    MarketSnapshot,
    MicroStructure,
    TradeAction,
    TradingSignal,
)
from icarus.signal.indicators import (
    bollinger_continuous,
    compute_atr,
    compute_choppiness,
    compute_volume_surge,
    detect_engulfing,
    engulfing_quality,
    rsi_continuous,
    volume_surge_quality,
)
from icarus.signal.market_state import compute_ranging_score
from icarus.signal.scoring import build_signal, safety_filters_ok

logger = logging.getLogger(__name__)


class ScalpingSignalEngine(SignalEngineInterface):
    """Moteur de signal pour le scalping 1m (implémentation de SignalEngine).

    Extrait les arrays numpy des Candle, calcule tous les indicateurs via
    les fonctions pures de ``indicators.py``, puis les combine avec le
    scoring de ``scoring.py``.

    Parameters
    ----------
    config: ScalpingConfig
        Configuration validée (seuils RSI, ATR, TP/SL, etc.).
    """

    def __init__(self, config: ScalpingConfig):
        self._config = config
        self._signals_generated: int = 0  # compteur
        self._last_signal_time: float = 0.0

    # ═════════════════════════════════════════════════════════════════════
    # Implémentation de l'interface SignalEngine
    # ═════════════════════════════════════════════════════════════════════

    def generate(self, snapshot: MarketSnapshot) -> TradingSignal:
        """Produit un signal à partir d'un snapshot de marché.

        Parameters
        ----------
        snapshot: MarketSnapshot
            Snapshot complet (candles + micro).

        Returns
        -------
        TradingSignal — action=WAIT si aucun signal valide.
        """
        try:
            # 1. Extraire les arrays numpy des Candle
            arrays = self._candles_to_arrays(snapshot.candles)

            # 2. Calculer les indicateurs
            indicators = self._compute_indicators(
                arrays, snapshot.micro
            )

            # 3. Filtres de sécurité
            ok, reason = safety_filters_ok(
                indicators,
                snapshot.micro,
                self._config.min_atr_percent,
                self._config.max_atr_percent,
                self._config.spread_max_percent,
            )

            if not ok:
                logger.debug(f"Signal rejeté (filtres): {reason}")
                return TradingSignal(
                    action=TradeAction.WAIT,
                    direction=None,
                    score=0.0,
                    entry=0.0, sl=0.0, tp1=0.0, tp2=0.0,
                    tp1_fraction=self._config.tp1_fraction,
                    reason=reason,
                    timestamp=time.time(),
                )

            # 4. Scoring et assemblage
            signal = build_signal(
                indicators=indicators,
                micro=snapshot.micro,
                base_threshold=self._config.threshold_base,
                fixed_sl=self._config.fixed_sl_percent,
                tp1=self._config.tp1_percent,
                tp2=self._config.tp2_percent,
                tp1_fraction=self._config.tp1_fraction,
            )

            if signal.action != TradeAction.WAIT:
                self._signals_generated += 1
                self._last_signal_time = time.time()
                logger.info(
                    f"✅ SIGNAL {signal.action.value} | Score:{signal.score:.1f} | "
                    f"Entry:{signal.entry} | SL:{signal.sl} | TP1:{signal.tp1} | TP2:{signal.tp2}"
                )

            return signal

        except Exception as e:
            logger.exception(f"[SignalEngine] Erreur lors de la génération: {e}")
            return TradingSignal(
                action=TradeAction.WAIT,
                direction=None,
                score=0.0,
                entry=0.0, sl=0.0, tp1=0.0, tp2=0.0,
                tp1_fraction=self._config.tp1_fraction,
                reason=f"Erreur interne: {e}",
                timestamp=time.time(),
            )

    def get_health(self) -> HealthStatus:
        """Retourne le statut de santé du SignalEngine."""
        age = time.time() - self._last_signal_time if self._last_signal_time > 0 else float("inf")

        if age > 3600:  # plus d'1h sans signal
            status = ComponentStatus.DEGRADED
            msg = f"Aucun signal depuis {age:.0f}s"
        else:
            status = ComponentStatus.HEALTHY
            msg = f"OK ({self._signals_generated} signaux générés)"

        return HealthStatus(
            component="ScalpingSignalEngine",
            status=status,
            message=msg,
            since=time.time(),
            metrics={
                "signals_generated": self._signals_generated,
                "last_signal_age": age,
            },
        )

    # ═════════════════════════════════════════════════════════════════════
    # Helpers privés
    # ═════════════════════════════════════════════════════════════════════

    @staticmethod
    def _candles_to_arrays(candles: tuple[Candle, ...]) -> dict[str, np.ndarray]:
        """Convertit un tuple de Candle en arrays numpy 1D.

        Returns
        -------
        dict avec les clés: ``open``, ``high``, ``low``, ``close``, ``volume``.
        """
        n = len(candles)
        opens   = np.zeros(n)
        highs   = np.zeros(n)
        lows    = np.zeros(n)
        closes  = np.zeros(n)
        volumes = np.zeros(n)

        for i, c in enumerate(candles):
            opens[i]   = c.open
            highs[i]   = c.high
            lows[i]    = c.low
            closes[i]  = c.close
            volumes[i] = c.volume

        return {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }

    def _compute_indicators(
        self, arrays: dict[str, np.ndarray], micro: MicroStructure
    ) -> IndicatorSuite:
        """Calcule tous les indicateurs à partir des arrays numpy.

        Parameters
        ----------
        arrays: dict
            Sortie de ``_candles_to_arrays``.
        micro: MicroStructure
            Microstructure (pour le mid_price).

        Returns
        -------
        IndicatorSuite complète.
        """
        closes  = arrays["close"]
        highs   = arrays["high"]
        lows    = arrays["low"]
        volumes = arrays["volume"]
        opens   = arrays["open"]

        # ── Bollinger ──
        bb = bollinger_continuous(closes, period=15, nb_std=2.0)

        # ── RSI ──
        rsi_data = rsi_continuous(
            closes, period=7,
            rsi_oversold=float(self._config.rsi_oversold),
            rsi_overbought=float(self._config.rsi_overbought),
        )

        # ── ATR ──
        atr_val = compute_atr(highs, lows, closes, period=10)
        mid = micro.mid_price if micro.mid_price > 0 else float(closes[-1])
        atr_pct = atr_val / mid if mid > 0 else 0.0

        # ── Volume Surge ──
        vol_surge_raw = compute_volume_surge(volumes, period=20)
        vol_surge_qual = volume_surge_quality(
            volumes, threshold=self._config.volume_surge_threshold, period=20
        )

        # ── Choppiness ──
        chop = compute_choppiness(highs, lows, closes, period=14)

        # ── Engulfing ──
        engulf_dir = detect_engulfing(opens, closes)
        engulf_q = engulfing_quality(opens, closes, atr_val)

        # ── Régime de marché ──
        regime = self._classify_regime(chop, atr_pct)

        # ── Ranging Score (market_state) ──
        ranging = compute_ranging_score(highs, lows, closes, period=10)

        # ── Ready ──
        ready = len(closes) >= 50 and atr_val > 0

        return IndicatorSuite(
            rsi=rsi_data["rsi"],
            percent_b=bb["percent_b"],
            atr=atr_val,
            atr_percent=atr_pct,
            volume_surge=vol_surge_raw,
            choppiness=chop,
            engulfing=engulf_dir,
            engulfing_quality=engulf_q.get("quality", 0.0),
            rsi_long_score=rsi_data["long_score"],
            rsi_short_score=rsi_data["short_score"],
            bollinger_long_score=bb["long_score"],
            bollinger_short_score=bb["short_score"],
            volume_surge_quality=vol_surge_qual,
            ready=ready,
            regime=regime,
            ranging_score=ranging,
        )

    @staticmethod
    def _classify_regime(choppiness: float, atr_pct: float) -> MarketRegime:
        """Classifie le régime de marché.

        - CHOPPINESS < 38.2 + ATR élevé  → TREND_UP / TREND_DOWN
        - CHOPPINESS < 38.2 + ATR faible → RANGE (tendance faible)
        - CHOPPINESS > 61.8              → RANGE
        - CHOPPINESS entre 38.2 et 61.8 + ATR élevé → VOLATILE

        Note: la direction (UP/DOWN) n'est pas déterminée ici — elle dépend
        du signal.  TREND_UP/TREND_DOWN est approximé par la position
        du prix dans les bandes de Bollinger (si on avait le percent_b sous
        la main).  Pour l'instant, on retourne TREND_UP par défaut pour
        les tendances (à affiner avec un ADX / slope de SMA dans le futur).
        """
        if choppiness < 38.2:
            if atr_pct > 0.004:
                return MarketRegime.TREND_UP  # direction à déterminer
            return MarketRegime.RANGE
        elif choppiness > 61.8:
            return MarketRegime.RANGE
        else:
            if atr_pct > 0.006:
                return MarketRegime.VOLATILE
            return MarketRegime.RANGE

    # ── Stats ───────────────────────────────────────────────────────────

    @property
    def signals_count(self) -> int:
        """Nombre total de signaux générés (hors WAIT)."""
        return self._signals_generated