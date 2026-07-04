#!/usr/bin/env python3
"""main.py — Point d'entrée du bot Icarus.

Usage:
    python main.py [--config config.yaml]

Le fichier principal est volontairement minimal (moins de 30 lignes).
Toute la logique est dans l'orchestrateur et les modules.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from icarus.orchestrator import Orchestrator


def setup_logging(level: str = "INFO", log_file: str = "icarus.log"):
    """Configure le logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )

    # Réduire le bruit des librairies externes
    for lib in ("ccxt", "websockets", "urllib3", "asyncio"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def main():
    """Point d'entrée principal."""
    parser = argparse.ArgumentParser(
        description="🤖 Icarus — Bot de scalping intraday automatisé",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Chemin vers le fichier de configuration YAML (défaut: config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de log (défaut: INFO)",
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Génère un fichier de configuration exemple et quitte.",
    )

    args = parser.parse_args()

    # Génération de config exemple
    if args.generate_config:
        from icarus.config.loader import generate_example_config
        generate_example_config(args.config)
        return

    # Setup logging
    setup_logging(level=args.log_level)

    logger = logging.getLogger("main")
    logger.info("🚀 Démarrage de Icarus...")

    # Windows compatibility
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Lancer l'orchestrateur
    orchestrator = Orchestrator(config_path=args.config)

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Arrêt manuel demandé (Ctrl+C).")
    except Exception as e:
        logger.exception(f"Erreur fatale: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()