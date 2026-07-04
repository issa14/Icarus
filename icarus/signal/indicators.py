"""icarus.signal.indicators — Fonctions pures de calcul d'indicateurs techniques.

╔══════════════════════════════════════════════════════════════════════════════╗
║  CONTRAT : Chaque fonction est PURE — pas d'état, pas de self, pas de DF.  ║
║  Entrée : numpy arrays 1D  │  Sortie : float (ou tuple de float).          ║
║  Testable indépendamment du reste du système.                               ║
╚══════════════════════════════════════════════════════════════════════════════╝

Tout le code provient de scalp_metrics.py, refactoré en fonctions standalone.
Les formules sont IDENTIQUES à la version de production.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
# 1. BOLLINGER BANDS
# ═══════════════════════════════════════════════════════════════════════════

def compute_bollinger(
    closes: np.ndarray, period: int = 15, nb_std: float = 2.0
) -> Dict[str, float]:
    """Calcule la position du prix dans les bandes de Bollinger.

    Parameters
    ----------
    closes: np.ndarray
        Série des prix de clôture (au moins ``period`` éléments).
    period: int
        Période de la moyenne mobile (défaut: 15).
    nb_std: float
        Nombre d'écarts-types pour les bandes (défaut: 2.0).

    Returns
    -------
    dict avec les clés: ``upper``, ``middle``, ``lower``, ``percent_b``.
    ``percent_b`` est clampé dans [0, 1].
    """
    if len(closes) < period:
        return {"upper": 0.0, "middle": 0.0, "lower": 0.0, "percent_b": 0.5}

    window = closes[-period:]
    sma = float(np.mean(window))
    std = float(np.std(window, ddof=0))

    upper = sma + nb_std * std
    lower = sma - nb_std * std
    last_close = float(closes[-1])

    if upper - lower != 0:
        percent_b = (last_close - lower) / (upper - lower)
    else:
        percent_b = 0.5

    # Clamp [0, 1]
    percent_b = max(0.0, min(1.0, percent_b))

    return {
        "upper": upper,
        "middle": sma,
        "lower": lower,
        "percent_b": percent_b,
    }


def bollinger_continuous(
    closes: np.ndarray, period: int = 15, nb_std: float = 2.0
) -> Dict[str, float]:
    """Version continue : scores long/short basés sur percent_b.

    Returns
    -------
    dict avec ``percent_b``, ``long_score``, ``short_score`` (tous dans [0,1]).
    """
    bb = compute_bollinger(closes, period, nb_std)
    percent_b = bb["percent_b"]

    # Long score: 1 quand percent_b=0, 0 quand >=0.2
    long_score = min(1.0, max(0.0, 1.0 - percent_b / 0.2))
    # Short score: 0 quand <=0.8, 1 quand =1
    short_score = min(1.0, max(0.0, (percent_b - 0.8) / 0.2))

    return {
        **bb,
        "long_score": long_score,
        "short_score": short_score,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 2. RSI (période 7)
# ═══════════════════════════════════════════════════════════════════════════

def compute_rsi(closes: np.ndarray, period: int = 7) -> float:
    """Calcule le RSI sur la dernière fenêtre ``period``.

    Utilise la moyenne conditionnelle (identique à scalp_metrics.py) :
    sum(gains) / count(positifs), pas sum(gains) / period.

    Parameters
    ----------
    closes: np.ndarray
        Au moins ``period + 1`` éléments.
    period: int
        Période du RSI (défaut: 7).

    Returns
    -------
    RSI entre 0 et 100. Retourne 50.0 si pas assez de données.
    """
    if len(closes) < period + 1:
        return 50.0

    window = closes[-(period + 1):]
    deltas = np.diff(window)

    gains = deltas[deltas > 0]
    losses = -deltas[deltas < 0]

    avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi)


def rsi_continuous(closes: np.ndarray, period: int = 7,
                   rsi_oversold: float = 35.0, rsi_overbought: float = 65.0) -> Dict[str, float]:
    """Scores RSI continus entre 0 et 1.

    Returns
    -------
    dict avec ``rsi``, ``long_score``, ``short_score``.
    """
    rsi_val = compute_rsi(closes, period)

    long_score = max(0.0, (rsi_oversold - rsi_val) / rsi_oversold) if rsi_val < rsi_oversold else 0.0
    denom = 100.0 - rsi_overbought
    short_score = max(0.0, (rsi_val - rsi_overbought) / denom) if denom > 0 and rsi_val > rsi_overbought else 0.0

    return {"rsi": rsi_val, "long_score": long_score, "short_score": short_score}


# ═══════════════════════════════════════════════════════════════════════════
# 3. ATR (Average True Range)
# ═══════════════════════════════════════════════════════════════════════════

def compute_atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 10
) -> float:
    """ATR sur la dernière fenêtre ``period``.

    Parameters
    ----------
    highs, lows, closes: np.ndarray
        Au moins ``period`` éléments chacun. Alignés temporellement.
    period: int
        Période (défaut: 10).

    Returns
    -------
    ATR en points de prix. 0.0 si pas assez de données.
    """
    if len(highs) < period or len(lows) < period or len(closes) < period + 1:
        return 0.0

    h = highs[-period:]
    l = lows[-period:]
    c = closes[-(period + 1):]

    tr = np.maximum(
        h - l,
        np.maximum(
            np.abs(h - c[:-1]),
            np.abs(l - c[:-1]),
        ),
    )

    return float(np.mean(tr))


# ═══════════════════════════════════════════════════════════════════════════
# 4. VOLUME SURGE
# ═══════════════════════════════════════════════════════════════════════════

def compute_volume_surge(volumes: np.ndarray, period: int = 20) -> float:
    """Ratio volume courant / moyenne mobile (exclut la bougie courante).

    Retourne 1.0 si pas assez de données ou moyenne nulle.
    """
    if len(volumes) < period + 1:
        return 1.0

    avg_vol = float(np.mean(volumes[-(period + 1):-1]))
    current_vol = float(volumes[-1])

    if avg_vol == 0:
        return 1.0
    return current_vol / avg_vol


def volume_surge_quality(volumes: np.ndarray, threshold: float = 1.8, period: int = 20) -> float:
    """Score continu [0,1] basé sur le volume surge.

    0 = volume normal, 1 = volume ≥ threshold.
    """
    raw = compute_volume_surge(volumes, period)
    quality = (raw - 1.0) / max(threshold - 1.0, 1e-9)
    return min(1.0, max(0.0, quality))


# ═══════════════════════════════════════════════════════════════════════════
# 5. CHOPPINESS INDEX
# ═══════════════════════════════════════════════════════════════════════════

def compute_choppiness(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> float:
    """Choppiness Index : 0-38 = tendance forte, 62-100 = range.

    Retourne 50.0 si pas assez de données.
    """
    if len(highs) < period or len(lows) < period or len(closes) < period + 1:
        return 50.0

    h = highs[-period:]
    l = lows[-period:]
    c = closes[-(period + 1):]

    tr = np.zeros(period)
    for i in range(period):
        hl = h[i] - l[i]
        prev_close = c[i]  # c[i] est le close de la bougie i de la fenêtre
        hc = abs(h[i] - prev_close)
        lc = abs(l[i] - prev_close)
        tr[i] = max(hl, hc, lc)

    sum_tr = float(np.sum(tr))
    max_high = float(np.max(h))
    min_low = float(np.min(l))

    if max_high - min_low == 0:
        return 50.0

    chop = 100.0 * np.log10(sum_tr / (max_high - min_low)) / np.log10(period)
    return max(0.0, min(100.0, chop))


# ═══════════════════════════════════════════════════════════════════════════
# 6. ENGULFING PATTERN
# ═══════════════════════════════════════════════════════════════════════════

def detect_engulfing(
    opens: np.ndarray, closes: np.ndarray
) -> int:
    """Détecte un motif d'engloutissement sur les 2 dernières bougies.

    Returns
    -------
    1  = Bullish Engulfing (signal LONG)
    -1 = Bearish Engulfing (signal SHORT)
    0  = Aucun pattern
    """
    if len(opens) < 2 or len(closes) < 2:
        return 0

    prev_open, curr_open = float(opens[-2]), float(opens[-1])
    prev_close, curr_close = float(closes[-2]), float(closes[-1])

    prev_bull = prev_close > prev_open
    prev_bear = prev_close < prev_open

    # Bullish Engulfing: bougie précédente baissière, courante haussière,
    # le corps courant englobe le corps précédent
    if prev_bear and curr_close > curr_open:
        if curr_open < prev_close and curr_close > prev_open:
            return 1

    # Bearish Engulfing
    if prev_bull and curr_close < curr_open:
        if curr_open > prev_close and curr_close < prev_open:
            return -1

    return 0


def engulfing_quality(
    opens: np.ndarray, closes: np.ndarray, atr_val: float
) -> Dict[str, float]:
    """Qualité du pattern engulfing (0-1) basée sur la taille du corps vs ATR.

    Returns
    -------
    dict avec ``direction`` (1/-1/0) et ``quality`` (0-1).
    """
    direction = detect_engulfing(opens, closes)
    if direction == 0 or atr_val <= 0:
        return {"direction": 0, "quality": 0.0}

    body = abs(float(closes[-1]) - float(opens[-1]))
    ratio = body / atr_val

    if ratio < 0.5:
        quality = 0.0
    elif ratio > 1.5:
        quality = 1.0
    else:
        quality = (ratio - 0.5) / (1.5 - 0.5)

    return {"direction": direction, "quality": quality}