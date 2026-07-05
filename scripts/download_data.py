#!/usr/bin/env python3
"""scripts/download_data.py — Télécharge les données OHLCV 1m depuis Binance (public).

Usage:
    python scripts/download_data.py              # SOL/USDT, HYPE/USDT, DOGE/USDT — 30 jours
    python scripts/download_data.py --symbols SOL/USDT,ETH/USDT
    python scripts/download_data.py --days 90

Les fichiers sont sauvegardés dans icarus/cache/
(ex: icarus/cache/SOL_USDT_1m_30d.csv)

Aucune clé API nécessaire — les données de marché sont publiques.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

# ── Ajoute le projet au PYTHONPATH ──────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("download_data")

CACHE_DIR = Path(__file__).resolve().parent.parent / "icarus" / "cache"

# ── Symboles par défaut ──────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["SOL/USDT", "HYPE/USDT", "DOGE/USDT"]
DEFAULT_DAYS = 30
TIMEFRAME = "1m"

# CCXT rate limit: Binance autorise ~1200 requêtes/min, on reste à 1 req/s
SLEEP_BETWEEN_CALLS = 0.2  # 200ms entre chaque chunk
CHUNK_SIZE_MS = 60 * 60 * 1000  # 1 heure en ms (1000 bougies max par call)
MS_PER_MINUTE = 60 * 1000


def main():
    parser = argparse.ArgumentParser(
        description="Télécharge les données OHLCV 1m depuis Binance"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help="Symboles séparés par des virgules (défaut: SOL/USDT,HYPE/USDT,DOGE/USDT)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Nombre de jours d'historique (défaut: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=str(CACHE_DIR),
        help=f"Répertoire de cache (défaut: {CACHE_DIR})",
    )
    args = parser.parse_args()

    symbols: List[str] = [s.strip() for s in args.symbols.split(",") if s.strip()]
    days: int = args.days
    cache_dir: Path = Path(args.cache_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"📦 Cache : {cache_dir}")
    logger.info(f"📅 Période : {days} jours")
    logger.info(f"🪙 Symboles : {symbols}")

    try:
        import ccxt
    except ImportError:
        logger.error(
            "ccxt n'est pas installé.  Exécute : pip install ccxt"
        )
        sys.exit(1)

    exchange = ccxt.binance({"enableRateLimit": True})

    for symbol in symbols:
        try:
            download_symbol(
                exchange, symbol, days, cache_dir
            )
        except Exception as e:
            logger.error(f"❌ Échec pour {symbol}: {e}")

    logger.info("🏁 Terminé.")


def download_symbol(
    exchange, symbol: str, days: int, cache_dir: Path
) -> None:
    """Télécharge tout l'historique pour un symbole, par chunk d'1 heure."""
    safe_name = symbol.replace("/", "_")
    filename = cache_dir / f"{safe_name}_{TIMEFRAME}_{days}d.csv"

    end_time = datetime.now(timezone.utc)
    # Aligner sur la minute pleine
    end_time = end_time.replace(second=0, microsecond=0)
    start_time = end_time - timedelta(days=days)

    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    logger.info(
        f"⬇️  {symbol} — {start_time.strftime('%Y-%m-%d')} → {end_time.strftime('%Y-%m-%d')} ({days}j)"
    )

    all_candles: list = []
    current_since = start_ms
    calls = 0

    # Vérifier que le symbole existe sur Binance
    try:
        exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=1)
    except Exception:
        logger.error(f"   ❌ {symbol} n'existe pas sur Binance — ignoré")
        return

    while current_since < end_ms:
        calls += 1
        try:
            ohlcv = exchange.fetch_ohlcv(
                symbol,
                timeframe=TIMEFRAME,
                since=current_since,
                limit=1000,  # Binance max = 1000
            )
        except Exception as e:
            logger.warning(f"   ⚠️  Erreur API (tentative {calls}): {e}")
            if calls > 10:
                logger.error(f"   ❌ Trop d'erreurs pour {symbol} — abandon")
                return
            time.sleep(2)
            continue

        if not ohlcv:
            logger.debug(f"   Plus de données à partir de {current_since}")
            break

        all_candles.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        current_since = last_ts + MS_PER_MINUTE

        # Log de progression toutes les 10 requêtes
        if calls % 10 == 0:
            pct = min(100, (last_ts - start_ms) / (end_ms - start_ms) * 100)
            logger.info(f"   {symbol}: {len(all_candles)} bougies ({pct:.0f}%)")

        time.sleep(SLEEP_BETWEEN_CALLS)

    if not all_candles:
        logger.error(f"   ❌ Aucune donnée reçue pour {symbol}")
        return

    # ── Écriture CSV ──
    lines = ["timestamp_ms,open,high,low,close,volume"]
    for c in sorted(all_candles, key=lambda c: c[0]):
        # c = [timestamp_ms, open, high, low, close, volume]
        lines.append(f"{c[0]},{c[1]},{c[2]},{c[3]},{c[4]},{c[5]}")

    filename.write_text("\n".join(lines), encoding="utf-8")

    # Statistiques
    first_ts = all_candles[0][0]
    last_ts = all_candles[-1][0]
    duration_sec = (last_ts - first_ts) / 1000
    expected = days * 24 * 60  # nombre théorique de bougies 1m
    coverage = len(all_candles) / max(expected, 1) * 100

    logger.info(
        f"   ✅ {symbol}: {len(all_candles)} bougies "
        f"({duration_sec / 3600:.0f}h, {coverage:.0f}% de couverture) → {filename.name}"
    )


if __name__ == "__main__":
    main()