"""icarus.config — Chargement et validation de la configuration.

Utilise Pydantic pour valider le fichier YAML au démarrage.
En cas d'erreur de configuration, le bot ne démarre pas.
"""

from icarus.config.models import BaseConfig, ScalpingConfig, ExchangeConfig
from icarus.config.loader import load_config, save_config