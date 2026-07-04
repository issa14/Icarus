"""icarus.signal.market_state — Détection de régime de marché (ranging vs trending).

Fonctions pures pour détecter si le marché est en train de ranger (faible amplitude,
pas de direction claire) ou en tendance (expansion directionnelle).

Le filtre de ranging est CRITIQUE pour le scalping : scalper dans un marché qui
varie de 0.1% sur 10 bougies = frais + spread > profit potentiel.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import numpy as np


def compute_range_amplitude(
    highs: np.ndarray, lows: np.ndarray, period: int = 10
) -> float:
    """Amplitude du range sur les N dernières bougies (en % du prix médian).

    Formule : (max_high - min_low) / median_price de la période.

    Returns
    -------
    Amplitude en pourcentage (ex: 0.003 = 0.3% sur 10 bougies).
    Retourne 0.0 si pas assez de données.
    """
    if len(highs) < period or len(lows) < period:
        return 0.0

    window_high = highs[-period:]
    window_low = lows[-period:]

    max_h = float(np.max(window_high))
    min_l = float(np.min(window_low))
    median_p = float(np.median(window_high + window_low) / 2.0)

    if median_p <= 0:
        return 0.0

    return (max_h - min_l) / median_p


def compute_ranging_score(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 10,
) -> float:
    """Score continu [0, 1] mesurant à quel point le marché est "rangeant".

    0.0 = marché directionnel, forte amplitude, tendance claire
    1.0 = marché complètement plat, aucune direction, très faible amplitude

    Le score combine :
    1. L'amplitude relative (faible amplitude → ranging)
    2. Le caractère oscillant (alternance hausse/baisse sans direction nette)
    3. La cohérence directionnelle (pente des closes)

    Parameters
    ----------
    highs, lows, closes: np.ndarray
        Arrays numpy 1D des prix.
    period: int
        Nombre de bougies analysées (défaut: 10).

    Returns
    -------
    Score entre 0.0 (trending) et 1.0 (ranging).
    """
    if len(highs) < period or len(lows) < period or len(closes) < period + 1:
        return 0.5  # neutre si pas assez de données

    # 1. Amplitude score (0 = grande amplitude, 1 = très faible amplitude)
    amplitude = compute_range_amplitude(highs, lows, period)

    # Seuils d'amplitude optimisés pour scalping 1m
    # < 0.0015 (0.15%) → marché trop calme pour scalper → ranging_score élevé
    # > 0.0050 (0.50%) → assez de mouvement → ranging_score bas
    if amplitude < 0.0015:
        amplitude_score = 1.0
    elif amplitude > 0.0050:
        amplitude_score = 0.0
    else:
        amplitude_score = 1.0 - (amplitude - 0.0015) / (0.0050 - 0.0015)
    amplitude_score = max(0.0, min(1.0, amplitude_score))

    # 2. Score d'oscillation (combien de changements de direction)
    # On compte les signes des deltas
    closes_window = closes[-(period + 1):]
    deltas = np.diff(closes_window)
    sign_changes = int(np.sum(np.diff(np.sign(deltas)) != 0))

    # 0 changement = tout dans la même direction (trending pur)
    # period-1 changements = oscillation complète (ranging pur)
    max_changes = period - 1
    if max_changes > 0:
        oscillation_score = sign_changes / max_changes
    else:
        oscillation_score = 0.5

    # 3. Score de cohérence directionnelle (pente des closes vs amplitude totale)
    # Une tendance nette a un delta cumulé proche de l'amplitude totale
    total_move = closes[-1] - closes[-(period + 1)]
    max_range = float(np.max(window_high := highs[-period:])) - float(np.min(window_low := lows[-period:]))

    if max_range > 0:
        direction_ratio = abs(total_move) / max_range
    else:
        direction_ratio = 0.0

    # direction_ratio proche de 1 → forte direction
    # direction_ratio proche de 0 → oscillation sans direction
    direction_score = 1.0 - min(1.0, direction_ratio / 0.7)

    # Score combiné : pondération
    # L'amplitude est le facteur le plus important (60%)
    # L'oscillation confirme (25%)
    # La cohérence directionnelle affine (15%)
    ranging = (
        0.60 * amplitude_score
        + 0.25 * oscillation_score
        + 0.15 * direction_score
    )

    return max(0.0, min(1.0, ranging))


def should_skip_ranging(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    threshold: float = 0.7,
    period: int = 10,
) -> tuple[bool, float, str]:
    """Décide si le marché doit être skipé car trop rangeant.

    Parameters
    ----------
    threshold: float
        Seuil au-dessus duquel on skip (défaut: 0.7).
    period: int
        Période de calcul (défaut: 10).

    Returns
    -------
    (skip, ranging_score, reason)
    """
    score = compute_ranging_score(highs, lows, closes, period)
    amplitude = compute_range_amplitude(highs, lows, period)

    if score >= threshold:
        return True, score, f"Marché rangeant (score={score:.2f}, amplitude={amplitude:.4f})"
    return False, score, ""


def expansion_detected(
    volumes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    surge_threshold: float = 1.8,
    period: int = 5,
) -> bool:
    """Détecte une expansion imminente (sortie de range avec volume).

    Conditions :
    1. Le marché était rangeant sur la période N-5 à N-1
    2. Un volume surge se produit sur la dernière bougie
    3. La bougie courante sort du range précédent

    Returns
    -------
    True si une expansion est détectée.
    """
    if len(volumes) < period + 5 or len(highs) < period + 5:
        return False

    # Marché rangeant sur les bougies -6 à -1 ?
    prev_ranging = compute_ranging_score(
        highs[-(period + 5):-1],
        lows[-(period + 5):-1],
        closes[-(period + 5):-1],
        period=period + 1,
    )

    if prev_ranging < 0.6:
        return False  # pas rangeant avant

    # Volume surge sur la dernière bougie ?
    avg_vol = float(np.mean(volumes[-(period + 1):-1]))
    current_vol = float(volumes[-1])
    if avg_vol > 0 and current_vol / avg_vol < surge_threshold:
        return False

    # La bougie courante sort du range précédent ?
    prev_high = float(np.max(highs[-(period + 1):-1]))
    prev_low = float(np.min(lows[-(period + 1):-1]))
    curr_close = float(closes[-1])

    if curr_close > prev_high or curr_close < prev_low:
        return True

    return False