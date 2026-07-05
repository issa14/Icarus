#!/usr/bin/env python3
"""icarus.backtest.optimizer — Grid search for BacktestEngine (Spot + Futures).

Grid search on the most sensitive parameters:
  Spot:
    1. threshold_base   (55-80)
    2. tp1_percent      (0.003-0.010)
    3. kelly_fraction   (0.10-0.30)

  Futures (--futures flag):
    4. leverage         (1-20)

Scoring function:
  score = 0.4 * return_pct + 0.3 * sharpe_ratio * 5.0 - 0.3 * max_drawdown_pct
  Bonus: trade_count >= 20 (liquidations penalized in futures mode)

Usage:
    python -m icarus.backtest.optimizer                    # Spot
    python -m icarus.backtest.optimizer --futures          # Futures
    python -m icarus.backtest.optimizer --futures --leverage 5,10,15

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import copy
import csv
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

import argparse

from icarus.backtest.engine import BacktestEngine
from icarus.backtest.futures_engine import FuturesBacktestEngine
from icarus.config.models import ScalpingConfig

logger = logging.getLogger(__name__)

# ── Search space ────────────────────────────────────────────────────────
THRESHOLD_RANGE = list(range(55, 85, 5))       # 55, 60, 65, 70, 75, 80
TP1_RANGE = [0.003, 0.004, 0.005, 0.006, 0.008, 0.010]
KELLY_RANGE = [0.10, 0.15, 0.20, 0.25, 0.30]
LEVERAGE_RANGE = [1, 2, 3, 5, 10, 20]          # Futures only
# Total Spot: 6 * 6 * 5 = 180 combos (~9 min)
# Total Futures: 180 * 6 = 1080 combos (~54 min)

# ── Cache & output dirs ──────────────────────────────────────────────────
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "icarus" / "cache"
OUTPUT_CSV = Path(__file__).resolve().parent / "optimizer_results.csv"
OUTPUT_CSV_FUTURES = Path(__file__).resolve().parent / "optimizer_results_futures.csv"


def optimizer_score(metrics: dict, *, is_futures: bool = False) -> float:
    """Composite score: higher is better.

    Futures mode penalizes liquidations heavily.
    """
    return_pct = metrics.get("total_return_pct", 0.0)
    sharpe = metrics.get("sharpe_ratio", 0.0)
    drawdown = abs(metrics.get("max_drawdown_pct", 0.0))

    # Trade count bonus
    n_trades = metrics.get("total_trades", 0)
    trade_bonus = min(1.0, n_trades / 20.0)

    # Liquidation penalty (futures only)
    liq_penalty = 1.0
    if is_futures:
        liquidations = metrics.get("total_liquidations", 0)
        liq_penalty = max(0.3, 1.0 - liquidations * 0.35)  # -0.35 per liquidation

    score = (0.4 * return_pct + 0.3 * sharpe * 5.0 - 0.3 * drawdown) * trade_bonus * liq_penalty
    return round(score, 2)


def optimizer_score_futures(metrics: dict) -> float:
    """Score for futures backtests (includes liquidation penalty)."""
    return optimizer_score(metrics, is_futures=True)


def load_default_config() -> ScalpingConfig:
    """Load default config from config.yaml or build a fallback."""
    try:
        from icarus.config.loader import load_config

        cfg = load_config("config.yaml")
        return cfg.scalping
    except Exception:
        logger.warning("config.yaml not found, using default config")
        return ScalpingConfig(
            symbols=["SOL/USDT"],
            execution_loop_seconds=0,
            tp1_percent=0.006,
            tp2_percent=0.010,
            fixed_sl_percent=0.004,
            tp1_fraction=0.6,
            trailing_callback=0.003,
            trailing_activation=0.5,
            entry_timeout_seconds=15,
            rsi_oversold=35,
            rsi_overbought=65,
            volume_surge_threshold=1.8,
            threshold_base=65.0,
            min_atr_percent=0.0015,
            max_atr_percent=0.010,
            spread_max_percent=0.0005,
            risk_per_trade=0.0025,
            max_daily_loss_percent=0.02,
            max_positions=1,
            kelly_fraction=0.15,
            cooldown_seconds=180,
            fee_maker=0.0002,
            fee_taker=0.0004,
            leverage=5,
        )


def grid_search_spot(
    csv_path: Path,
    symbol: str,
    base_config: ScalpingConfig,
) -> List[dict]:
    """Grid search for Spot mode: threshold_base, tp1, kelly_fraction."""
    results: list[dict] = []
    total = len(THRESHOLD_RANGE) * len(TP1_RANGE) * len(KELLY_RANGE)
    count = 0

    logger.info(f"🔍 Spot Grid Search — {total} combinations on {symbol}")
    logger.info(f"   CSV: {csv_path.name}")
    t_start = time.time()

    for threshold in THRESHOLD_RANGE:
        for tp1 in TP1_RANGE:
            for kelly in KELLY_RANGE:
                count += 1

                config = copy.deepcopy(base_config)
                config.threshold_base = float(threshold)
                config.tp1_percent = tp1
                config.kelly_fraction = kelly
                config.leverage = 1  # Spot = no leverage
                config.fixed_sl_percent = round(tp1 / 1.5, 4)

                try:
                    engine = BacktestEngine(config)
                    result = engine.run(csv_path, symbol, initial_balance=100.0)
                    metrics = result.metrics
                    score = optimizer_score(metrics)

                    result_row = {
                        "threshold_base": threshold,
                        "tp1_percent": tp1,
                        "fixed_sl_percent": config.fixed_sl_percent,
                        "kelly_fraction": kelly,
                        "score": score,
                        "trades": metrics["total_trades"],
                        "win_rate": metrics["win_rate"],
                        "total_return_pct": metrics["total_return_pct"],
                        "sharpe_ratio": metrics["sharpe_ratio"],
                        "max_drawdown_pct": metrics["max_drawdown_pct"],
                        "expectancy_usd": metrics["expectancy_usd"],
                        "profit_factor": metrics["profit_factor"],
                        "final_balance": metrics["final_balance"],
                    }
                    results.append(result_row)

                    pct_done = count / total * 100
                    elapsed = time.time() - t_start
                    eta = (elapsed / count) * (total - count) if count > 0 else 0

                    if count % 20 == 0 or count == 1:
                        logger.info(
                            f"   [{count:3d}/{total}] {pct_done:4.0f}% | "
                            f"Th={threshold} TP1={tp1:.4f} Kelly={kelly:.2f} "
                            f"→ Score={score:.1f} | ETA={eta:.0f}s"
                        )

                except Exception as e:
                    logger.warning(f"   ⚠️  Failed Th={threshold} TP1={tp1}: {e}")

    elapsed = time.time() - t_start
    logger.info(f"   ✅ Done in {elapsed:.0f}s ({count}/{total} runs)")

    return results


def grid_search_futures(
    csv_path: Path,
    symbol: str,
    base_config: ScalpingConfig,
    leverage_range: Optional[List[int]] = None,
) -> List[dict]:
    """Grid search for Futures mode: threshold_base, tp1, kelly_fraction, leverage."""
    if leverage_range is None:
        leverage_range = LEVERAGE_RANGE

    results: list[dict] = []
    total = len(THRESHOLD_RANGE) * len(TP1_RANGE) * len(KELLY_RANGE) * len(leverage_range)
    count = 0

    logger.info(f"🔍 Futures Grid Search — {total} combinations on {symbol}")
    logger.info(f"   CSV: {csv_path.name} | Leverage: {leverage_range}")
    t_start = time.time()

    for threshold in THRESHOLD_RANGE:
        for tp1 in TP1_RANGE:
            for kelly in KELLY_RANGE:
                for lev in leverage_range:
                    count += 1

                    config = copy.deepcopy(base_config)
                    config.threshold_base = float(threshold)
                    config.tp1_percent = tp1
                    config.kelly_fraction = kelly
                    config.leverage = lev
                    config.fixed_sl_percent = round(tp1 / 1.5, 4)

                    try:
                        engine = FuturesBacktestEngine(config)
                        result = engine.run(csv_path, symbol, initial_balance=100.0)
                        metrics = result.metrics
                        score = optimizer_score_futures(metrics)

                        result_row = {
                            "threshold_base": threshold,
                            "tp1_percent": tp1,
                            "fixed_sl_percent": config.fixed_sl_percent,
                            "kelly_fraction": kelly,
                            "leverage": lev,
                            "score": score,
                            "trades": metrics["total_trades"],
                            "liquidations": metrics.get("total_liquidations", 0),
                            "win_rate": metrics["win_rate"],
                            "total_return_pct": metrics["total_return_pct"],
                            "sharpe_ratio": metrics["sharpe_ratio"],
                            "max_drawdown_pct": metrics["max_drawdown_pct"],
                            "expectancy_usd": metrics["expectancy_usd"],
                            "profit_factor": metrics["profit_factor"],
                            "final_balance": metrics["final_balance"],
                        }
                        results.append(result_row)

                        pct_done = count / total * 100
                        elapsed = time.time() - t_start
                        eta = (elapsed / count) * (total - count) if count > 0 else 0

                        if count % 50 == 0 or count == 1:
                            logger.info(
                                f"   [{count:4d}/{total}] {pct_done:4.0f}% | "
                                f"Th={threshold} TP1={tp1:.4f} Kelly={kelly:.2f} Lev={lev}x "
                                f"→ Score={score:.1f} | ETA={eta:.0f}s"
                            )

                    except Exception as e:
                        logger.warning(
                            f"   ⚠️  Failed Th={threshold} TP1={tp1} Lev={lev}x: {e}"
                        )

    elapsed = time.time() - t_start
    logger.info(f"   ✅ Done in {elapsed:.0f}s ({count}/{total} runs)")

    return results


def save_results(results: List[dict], output_path: Path, *, is_futures: bool = False):
    """Save results to CSV sorted by score."""
    if not results:
        logger.warning("No results to save.")
        return

    sorted_results = sorted(results, key=lambda r: r["score"], reverse=True)

    fieldnames = list(sorted_results[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted_results)

    logger.info(f"📄 Results saved to {output_path}")

    # Display top 5
    mode = "FUTURES" if is_futures else "LEGACY"
    print("\n" + "=" * 80)
    print(f"🏆 TOP 5 — BEST PARAMS ({mode})")
    print("=" * 80)
    for i, r in enumerate(sorted_results[:5], 1):
        lev_str = f"  Lev={r.get('leverage', '?')}x" if is_futures else ""
        liq_str = f"  Liq={r.get('liquidations', '?')}" if is_futures else ""
        print(
            f"  #{i}  Score={r['score']:>6.1f}  "
            f"Th={r['threshold_base']}  TP1={r['tp1_percent']:.4f}  "
            f"SL={r['fixed_sl_percent']:.4f}  Kelly={r['kelly_fraction']:.2f}"
            f"{lev_str}  "
            f"Return={r['total_return_pct']:>+5.1f}%  "
            f"Sharpe={r['sharpe_ratio']:.2f}  DD={r['max_drawdown_pct']:.1f}%  "
            f"Trades={r['trades']}{liq_str}"
        )
    print("=" * 80)


def detect_csv() -> tuple[Optional[Path], Optional[str]]:
    """Detect available CSV in cache dir."""
    for sym, fname in [
        ("SOL/USDT", "SOL_USDT_1m_30d.csv"),
        ("HYPE/USDT", "HYPE_USDT_1m_30d.csv"),
        ("DOGE/USDT", "DOGE_USDT_1m_30d.csv"),
    ]:
        candidate = CACHE_DIR / fname
        if candidate.exists():
            return candidate, sym
    return None, None


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Icarus Backtest Optimizer — Grid search for Spot and Futures modes"
    )
    parser.add_argument(
        "--futures", action="store_true",
        help="Run in Futures mode (includes leverage sweep)",
    )
    parser.add_argument(
        "--leverage", type=str, default=None,
        help="Comma-separated leverage values to test (e.g. '2,5,10'). Default: 1,2,3,5,10,20",
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="Path to CSV data file (auto-detect from cache if omitted)",
    )
    parser.add_argument(
        "--symbol", type=str, default=None,
        help="Symbol name e.g. SOL/USDT (auto-detect if omitted)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Detect CSV
    csv_path = args.csv
    symbol = args.symbol
    if csv_path is None:
        csv_path, symbol = detect_csv()
        csv_path = Path(csv_path) if csv_path else None

    if csv_path is None:
        logger.error(
            "No CSV found in icarus/cache/. Run first:\n"
            "   python scripts/download_data.py"
        )
        sys.exit(1)

    is_futures = args.futures

    logger.info(f"📊 Optimization on {symbol} ({csv_path.name}) | Mode: {'Futures' if is_futures else 'Spot'}")

    base_config = load_default_config()

    if is_futures:
        # Parse custom leverage range if provided
        lev_range = None
        if args.leverage:
            lev_range = [int(x.strip()) for x in args.leverage.split(",") if x.strip()]
            logger.info(f"   Custom leverage range: {lev_range}")
        results = grid_search_futures(csv_path, symbol, base_config, lev_range)
        save_results(results, OUTPUT_CSV_FUTURES, is_futures=True)
    else:
        results = grid_search_spot(csv_path, symbol, base_config)
        save_results(results, OUTPUT_CSV)


if __name__ == "__main__":
    main()