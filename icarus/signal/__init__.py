"""icarus.signal — Moteur de génération de signaux de trading.

Architecture modulaire en 4 couches :
  1. indicators   → Fonctions pures, testables unitairement
  2. market_state → Détection ranging/trending (filtre anti-range)
  3. scoring      → Scoring continu (0-100) combinant les indicateurs
  4. engine       → Implémentation de l'interface SignalEngine
"""

from icarus.signal.engine import ScalpingSignalEngine
from icarus.signal.market_state import (
    compute_range_amplitude,
    compute_ranging_score,
    should_skip_ranging,
    expansion_detected,
)
