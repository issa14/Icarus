"""icarus.config.loader — Chargement et sauvegarde de la configuration YAML.

Les fichiers YAML sont chargés puis validés avec les modèles Pydantic.
En cas d'erreur de validation, une exception explicite est levée AVANT
que le bot ne démarre (fail-fast).

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from icarus.config.models import BaseConfig, ScalpingConfig, ExchangeConfig, TelegramConfig

logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> BaseConfig:
    """Charge et valide la configuration depuis un fichier YAML.

    Parameters
    ----------
    path: str
        Chemin vers le fichier de configuration YAML.

    Returns
    -------
    BaseConfig
        Configuration validée.

    Raises
    ------
    FileNotFoundError
        Si le fichier n'existe pas.
    yaml.YAMLError
        Si le fichier YAML est mal formé.
    pydantic.ValidationError
        Si la configuration ne respecte pas les contraintes (valeurs hors plage,
        incohérences TP1/TP2/SL, etc.).
    """
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Fichier de configuration introuvable: {config_path.resolve()}\n"
            f"Créez un fichier '{path}' ou copiez config_example.yaml."
        )

    logger.info(f"Chargement de la configuration depuis {config_path.resolve()}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raw = {}

    # Compatibilité avec l'ancien format (scalping_config au lieu de scalping)
    if "scalping_config" in raw and "scalping" not in raw:
        logger.warning(
            "Clé 'scalping_config' détectée — renommée en 'scalping' pour le nouveau format. "
            "Mettez à jour votre config.yaml."
        )
        raw["scalping"] = raw.pop("scalping_config")

    # Si l'ancien format avait exchange/config clés à plat, on adapte
    if "symbol" in raw and "scalping" not in raw:
        # Ancien format sans nesting → on wrap
        logger.warning(
            "Format de config legacy détecté (pas de section 'scalping'). "
            "Adaptation automatique — mettez à jour votre config.yaml."
        )
        scalping_raw = {}
        for key in ScalpingConfig.model_fields:
            if key in raw:
                scalping_raw[key] = raw.pop(key)
        raw["scalping"] = scalping_raw

    try:
        config = BaseConfig(**raw)
    except Exception as e:
        logger.error(f"Validation de la configuration échouée: {e}")
        raise

    logger.info("✅ Configuration validée avec succès.")
    logger.debug(f"Configuration chargée: {config.model_dump()}")

    return config


def save_config(config: BaseConfig, path: str) -> None:
    """Sauvegarde la configuration au format YAML.

    Parameters
    ----------
    config: BaseConfig
        Configuration à sauvegarder.
    path: str
        Chemin du fichier de destination.
    """
    config_path = Path(path)
    # Créer le dossier parent si nécessaire
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump()
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    logger.info(f"💾 Configuration sauvegardée dans {config_path.resolve()}")


def generate_example_config(path: str = "config.yaml") -> None:
    """Génère un fichier de configuration exemple avec les valeurs par défaut.

    Utile pour initialiser un nouveau projet ou montrer toutes les options disponibles.
    Ne l'utilise PAS comme configuration de production sans revoir chaque paramètre !
    """
    config = BaseConfig()  # Valeurs par défaut
    save_config(config, path)
    logger.info(f"📄 Configuration exemple générée: {path}")
    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  Fichier de configuration exemple créé : {path:<38}║
║                                                                              ║
║  ⚠️  ATTENTION : Ce fichier contient les valeurs PAR DÉFAUT.                ║
║  Avant de lancer le bot, PERSONNALISEZ au minimum :                          ║
║    • scalping.symbol           → paire tradée                               ║
║    • scalping.risk_per_trade   → risque par trade adapté à votre capital    ║
║    • exchange.api_key          → votre clé API                              ║
║    • exchange.api_secret       → votre secret API                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")