#!/usr/bin/env python3
"""Quick futures backtest runner using existing OHLCV data.

Supports leverage simulation, margin tracking, and liquidation monitoring.

Usage:
    python run_backtest_futures.py                          # default: SOL/USDT, 5x lev
    python run_backtest_futures.py --leverage 10            # 10x leverage
    python run_backtest_futures.py --symbol HYPE/USDT       # different symbol
    python run_backtest_futures.py --days 90                # download 90d data first
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from icarus.config.loader import load_config
from icarus.backtest.futures_engine import FuturesBacktestEngine


def format_metrics(metrics: dict) -> str:
    """Format metrics for display."""
    lines = []

    # Performance
    lines.append(f"  Final Balance:        ${metrics.get('final_balance', 0):,.2f}")
    lines.append(f"  Total Return:         {metrics.get('total_return_percent', 0):.2f}%")
    lines.append(f"  Win Rate:             {metrics.get('win_rate', 0):.1f}%")

    # Trades
    lines.append(f"\n  Total Trades:         {metrics.get('total_trades', 0)}")
    lines.append(f"  Winning Trades:       {metrics.get('wins', 0)}")
    lines.append(f"  Losing Trades:        {metrics.get('losses', 0)}")
    lines.append(f"  Liquidations:         {metrics.get('total_liquidations', 0)} 💀")
    lines.append(f"  Avg Profit/Trade:     ${metrics.get('avg_win_usd', 0):.2f}")

    # Risk
    lines.append(f"\n  Max Drawdown:         {metrics.get('max_drawdown_pct', 0):.2f}%")
    lines.append(f"  Sharpe Ratio:         {metrics.get('sharpe_ratio', 0):.2f}")
    lines.append(f"  Profit Factor:        {metrics.get('profit_factor', 0):.2f}")

    # Signals
    lines.append(f"\n  Signals Generated:    {metrics.get('signals_total', 0)}")
    lines.append(f"  Signals Traded:       {metrics.get('signals_traded', 0)}")
    lines.append(f"  Signals Skipped:      {metrics.get('signals_skipped', 0)}")

    return "\n".join(lines)


def ensure_data(symbol: str, days: int) -> Path:
    """Ensure CSV data exists, download if missing."""
    safe_name = symbol.replace("/", "_")
    data_file = Path(f"icarus/cache/{safe_name}_1m_{days}d.csv")

    if data_file.exists():
        logger.info(f"✓ Found data file: {data_file}")
        return data_file

    logger.warning(f"Data file not found: {data_file}")
    logger.info(f"⏳ Downloading {days}d of {symbol} data...")

    result = subprocess.run(
        [sys.executable, "scripts/download_data.py", "--symbols", symbol, "--days", str(days)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"Download failed:\n{result.stderr}")
        sys.exit(1)

    if not data_file.exists():
        logger.error(f"Download completed but file still missing: {data_file}")
        sys.exit(1)

    logger.info(f"✓ Data downloaded: {data_file}")
    return data_file


def main():
    parser = argparse.ArgumentParser(
        description="Icarus Futures Backtest — Leverage-aware scalping simulation"
    )
    parser.add_argument(
        "--symbol", type=str, default="SOL/USDT",
        help="Trading pair (default: SOL/USDT)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Days of historical data to use (default: 30)",
    )
    parser.add_argument(
        "--leverage", type=int, default=5,
        help="Leverage multiplier (default: 5)",
    )
    parser.add_argument(
        "--balance", type=float, default=100.0,
        help="Initial balance in USDT (default: 100.0)",
    )
    parser.add_argument(
        "--config", type=str, default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print(f"ICARUS FUTURES BACKTEST — {args.symbol} | {args.leverage}x Leverage")
    print("=" * 80)

    # Ensure data exists
    data_file = ensure_data(args.symbol, args.days)

    # Load config
    try:
        config_path = Path(args.config)
        if not config_path.exists():
            logger.info(f"{args.config} not found, using defaults from config.yaml.example")
            config_path = Path("config.yaml.example")

        config = load_config(str(config_path))
        scalping_cfg = config.scalping
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    # Override leverage from CLI
    scalping_cfg.leverage = args.leverage
    logger.info(f"✓ Leverage set to: {args.leverage}x")

    # Initialize futures backtest engine
    engine = FuturesBacktestEngine(scalping_cfg)
    logger.info("✓ Futures backtest engine initialized")

    # Run backtest
    print("\n" + "─" * 80)
    print(f"Running Futures Backtest ({args.leverage}x leverage)...")
    print("─" * 80 + "\n")

    result = engine.run(
        csv_path=data_file,
        symbol=args.symbol,
        initial_balance=args.balance,
    )

    # Display results
    print("\n" + "=" * 80)
    print(f"FUTURES BACKTEST RESULTS — {result.symbol}")
    print("=" * 80)

    print(f"\nConfiguration:")
    print(f"  Initial Balance:      ${args.balance:.2f}")
    print(f"  Leverage:             {args.leverage}x")
    print(f"  Margin Mode:          {scalping_cfg.margin_mode}")
    print(f"  Test Duration:        {result.elapsed_seconds:.1f}s")

    print(f"\nStrategy Parameters:")
    print(f"  TP1:                  {scalping_cfg.tp1_percent*100:.2f}%")
    print(f"  TP2:                  {scalping_cfg.tp2_percent*100:.2f}%")
    print(f"  SL:                   {scalping_cfg.fixed_sl_percent*100:.2f}%")
    print(f"  TP1 Fraction:         {scalping_cfg.tp1_fraction*100:.0f}%")
    print(f"  Kelly Fraction:       {scalping_cfg.kelly_fraction*100:.0f}%")
    print(f"  Risk/Trade (Kelly):   {scalping_cfg.risk_per_trade*100:.2f}%")

    if result.metrics:
        print(f"\nPerformance Metrics:")
        print(format_metrics(result.metrics))

    if result.trades:
        print(f"\n\nRecent Trades (last 10):")
        print("─" * 80)
        for trade in result.trades[-10:]:
            direction_str = trade['direction'] if isinstance(trade['direction'], str) else trade['direction'].value
            direction = "LONG" if direction_str in ('LONG', 'long') else "SHORT"
            entry_time = str(trade['entry_time'])
            lev = trade.get('leverage', '?')
            liq = trade.get('liquidation_price', 0)
            margin = trade.get('margin_used', 0)

            liq_str = f" | Liq: ${liq:.4f}" if liq else ""
            margin_str = f" | Margin: ${margin:.2f}" if margin else ""

            print(f"  {entry_time[:19]} | {direction} ({lev}x) @ ${trade['entry_price']:.4f} → ${trade['exit_price']:.4f} | "
                  f"PnL: ${trade['pnl_usd']:+.2f} ({trade.get('pnl_percent', 0):+.1f}%) | "
                  f"{trade['exit_reason']}{liq_str}{margin_str}")
    else:
        print(f"\n❌ No completed trades during backtest.")

    if result.total_liquidations > 0:
        print(f"\n⚠️  LIQUIDATIONS: {result.total_liquidations} trade(s) liquidated!")

    print("\n" + "=" * 80)

    final_balance = result.metrics.get('final_balance', args.balance)
    ret_pct = (final_balance - args.balance) / args.balance * 100
    if ret_pct > 0:
        print(f"✅ PROFITABLE: +{ret_pct:.2f}% return with {args.leverage}x leverage")
    else:
        print(f"❌ LOSS: {ret_pct:.2f}% return with {args.leverage}x leverage")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()