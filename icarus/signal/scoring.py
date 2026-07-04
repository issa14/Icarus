"""icarus.signal.scoring — Fonctions pures de scoring des signaux.

Extrait de signal_factory.py.  Logique IDENTIQUE à la production :
  • MEMES pondérations (WEIGHTS)
  • MEMES scores continus (RSI, Bollinger, Volume, Engulfing, Choppiness)
  • MEME seuil adaptatif basé sur l'ATR

V2: Pénalité de ranging (market_state.py) intégrée.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

from icarus.core.types import (
    Direction,
    IndicatorSuite,
    MicroStructure,
    TradingSignal,
    TradeAction,
)

logger = logging.getLogger(__name__)


# ── Pondérations de production (NE PAS MODIFIER sans recalibration complète) ──
WEIGHTS = {
    "rsi_extreme": 0.25,
    "bollinger_touch": 0.30,
    "engulfing": 0.20,
    "volume_surge": 0.15,
    "trend_confirmation": 0.10,
}
MAX_SCORE = 100.0


# ═══════════════════════════════════════════════════════════════════════════
# 1. SCORE LONG
# ═══════════════════════════════════════════════════════════════════════════

def score_long(indicators: IndicatorSuite, micro: MicroStructure) -> float:
    """Calcule le score de conviction pour un signal LONG (0-100)."""
    score = 0.0

    score += WEIGHTS["rsi_extreme"] * MAX_SCORE * indicators.rsi_long_score
    score += WEIGHTS["bollinger_touch"] * MAX_SCORE * indicators.bollinger_long_score

    if indicators.engulfing == 1:
        score += WEIGHTS["engulfing"] * MAX_SCORE * indicators.engulfing_quality

    score += WEIGHTS["volume_surge"] * MAX_SCORE * indicators.volume_surge_quality

    if indicators.choppiness < 38.2:
        score += WEIGHTS["trend_confirmation"] * MAX_SCORE

    if micro.imbalance > 0.6:
        score += 5.0

    return min(score, MAX_SCORE)


# ═══════════════════════════════════════════════════════════════════════════
# 2. SCORE SHORT
# ═══════════════════════════════════════════════════════════════════════════

def score_short(indicators: IndicatorSuite, micro: MicroStructure) -> float:
    """Calcule le score de conviction pour un signal SHORT (0-100)."""
    score = 0.0

    score += WEIGHTS["rsi_extreme"] * MAX_SCORE * indicators.rsi_short_score
    score += WEIGHTS["bollinger_touch"] * MAX_SCORE * indicators.bollinger_short_score

    if indicators.engulfing == -1:
        score += WEIGHTS["engulfing"] * MAX_SCORE * indicators.engulfing_quality

    score += WEIGHTS["volume_surge"] * MAX_SCORE * indicators.volume_surge_quality

    if indicators.choppiness < 38.2:
        score += WEIGHTS["trend_confirmation"] * MAX_SCORE

    if micro.imbalance < 0.4:
        score += 5.0

    return min(score, MAX_SCORE)


# ═══════════════════════════════════════════════════════════════════════════
# 3. SEUIL ADAPTATIF (volatilité)
# ═══════════════════════════════════════════════════════════════════════════

def adaptive_threshold(atr_percent: float, base_threshold: float) -> float:
    """Ajuste le seuil de déclenchement en fonction de l'ATR.

    - ATR bas (< 0.2%)  → seuil abaissé de 5 pts (plus permissif)
    - ATR haut (> 0.6%) → seuil relevé de 5 pts (plus strict)
    """
    if atr_percent < 0.002:
        return base_threshold - 5.0
    elif atr_percent > 0.006:
        return base_threshold + 5.0
    return base_threshold


# ═══════════════════════════════════════════════════════════════════════════
# 4. CALCUL DES NIVEAUX (Entry, SL, TP1, TP2)
# ═══════════════════════════════════════════════════════════════════════════

def calculate_levels(
    mid_price: float,
    direction: Direction,
    fixed_sl: float,
    tp1: float,
    tp2: float,
    atr_percent: float = 0.002,
) -> Dict[str, float]:
    """Calcule les prix opérationnels (entry limit, SL, TP1, TP2)."""
    entry_offset = min(0.0005, max(0.0001, atr_percent * 0.08))

    if direction == Direction.LONG:
        entry = mid_price * (1 - entry_offset)
        sl = entry * (1 - fixed_sl)
        tp1_price = entry * (1 + tp1)
        tp2_price = entry * (1 + tp2)
    else:
        entry = mid_price * (1 + entry_offset)
        sl = entry * (1 + fixed_sl)
        tp1_price = entry * (1 - tp1)
        tp2_price = entry * (1 - tp2)

    return {
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1_price, 2),
        "tp2": round(tp2_price, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. FILTRES DE SÉCURITÉ (anti-slippage, anti-range)
# ═══════════════════════════════════════════════════════════════════════════

def safety_filters_ok(
    indicators: IndicatorSuite,
    micro: MicroStructure,
    min_atr: float,
    max_atr: float,
    max_spread: float,
) -> Tuple[bool, str]:
    """Vérifie les filtres stricts avant d'accepter un signal."""
    if not indicators.ready:
        return False, "Données insuffisantes"

    atr_pct = indicators.atr_percent
    if not (min_atr <= atr_pct <= max_atr):
        return False, f"ATR hors plage ({atr_pct:.4f})"

    if micro.spread > max_spread:
        return False, f"Spread trop large ({micro.spread:.5f})"

    if indicators.choppiness > 61.8:
        return False, f"Marché en range (Choppiness={indicators.choppiness:.1f})"

    # Filtre ranging avancé (market_state)
    if indicators.ranging_score >= 0.75:
        return False, f"Marché trop rangeant (ranging={indicators.ranging_score:.2f})"

    if indicators.engulfing != 0 and atr_pct < 0.0008:
        return False, "Engulfing sur marché trop plat"

    return True, ""


# ═══════════════════════════════════════════════════════════════════════════
# 6. ASSEMBLAGE FINAL DU SIGNAL
# ═══════════════════════════════════════════════════════════════════════════

def build_signal(
    indicators: IndicatorSuite,
    micro: MicroStructure,
    base_threshold: float,
    fixed_sl: float,
    tp1: float,
    tp2: float,
    tp1_fraction: float = 0.6,
) -> TradingSignal:
    """Point d'entrée unique : évalue les scores et retourne un TradingSignal."""
    threshold = adaptive_threshold(indicators.atr_percent, base_threshold)

    sl = score_long(indicators, micro)
    ss = score_short(indicators, micro)

    # Pénalité de ranging : si ranging_score > 0.6, on réduit le score
    ranging_penalty = 1.0 - min(1.0, max(0.0, indicators.ranging_score - 0.6) / 0.4)
    sl *= ranging_penalty
    ss *= ranging_penalty

    direction = None
    score = 0.0

    if sl >= threshold and sl > ss:
        direction = Direction.LONG
        score = sl
    elif ss >= threshold and ss > sl:
        direction = Direction.SHORT
        score = ss
    else:
        return TradingSignal(
            action=TradeAction.WAIT,
            direction=None,
            score=0.0,
            entry=0.0,
            sl=0.0,
            tp1=0.0,
            tp2=0.0,
            tp1_fraction=tp1_fraction,
            reason=f"Score insuffisant (L:{sl:.1f}, S:{ss:.1f}, seuil:{threshold:.1f})",
            timestamp=time.time(),
        )

    levels = calculate_levels(
        mid_price=micro.mid_price,
        direction=direction,
        fixed_sl=fixed_sl,
        tp1=tp1,
        tp2=tp2,
        atr_percent=indicators.atr_percent,
    )

    return TradingSignal(
        action=TradeAction.BUY if direction == Direction.LONG else TradeAction.SELL,
        direction=direction,
        score=score,
        entry=levels["entry"],
        sl=levels["sl"],
        tp1=levels["tp1"],
        tp2=levels["tp2"],
        tp1_fraction=tp1_fraction,
        reason=f"{direction.value} signal | score={score:.1f} (≥{threshold:.1f})",
        timestamp=time.time(),
    )