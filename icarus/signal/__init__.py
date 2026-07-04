"""icarus.signal — Moteur de génération de signaux de trading.

Architecture modulaire en 3 couches :
  1. indicators  → Fonctions pures, testables unitairement
  2. scoring     → Scoring continu (0-100) combinant les indicateurs
  3. engine      → Implémentation de l'interface SignalEngine
"""

from icarus.signal.engine import ScalpingSignalEngine