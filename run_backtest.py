#!/usr/bin/env python3
"""Quick backtest runner using existing SOL/USDT data.

LEGACY / SPOT-ONLY — Ce script utilise le BacktestEngine spot (icarus.backtest.engine).
Il n'est PAS utilisé par le pipeline futures actuel.
Pour le backtesting futures, utilisez run_backtest_futures.py.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from icarus.config.loader import load_config
from icarus.backtest.engine import BacktestEngine


def format_metrics(metrics: dict) -> str:
    """Format metrics for display."""
    lines = []
    
    # Performance
    lines.append(f"  Final Balance:        ${metrics.get('final_balance', 0):,.2f}")
    lines.append(f"  Total Return:         {metrics.get('total_return_percent', 0):.2f}%")
    lines.append(f"  Win Rate:             {metrics.get('win_rate', 0):.1f}%")
    
    # Trades
    lines.append(f"\n  Total Trades:         {metrics.get('num_trades', 0)}")
    lines.append(f"  Winning Trades:       {metrics.get('num_winners', 0)}")
    lines.append(f"  Losing Trades:        {metrics.get('num_losers', 0)}")
    lines.append(f"  Avg Profit/Trade:     ${metrics.get('avg_profit_per_trade', 0):.2f}")
    
    # Risk
    lines.append(f"\n  Max Drawdown:         {metrics.get('max_drawdown_percent', 0):.2f}%")
    lines.append(f"  Sharpe Ratio:         {metrics.get('sharpe_ratio', 0):.2f}")
    lines.append(f"  Profit Factor:        {metrics.get('profit_factor', 0):.2f}")
    
    # Signals
    lines.append(f"\n  Signals Generated:    {metrics.get('signals_total', 0)}")
    lines.append(f"  Signals Traded:       {metrics.get('signals_traded', 0)}")
    lines.append(f"  Signals Skipped:      {metrics.get('signals_skipped', 0)}")
    
    return "\n".join(lines)


def main():
    """Run backtest."""
    print("\n" + "=" * 80)
    print("ICARUS BACKTEST - SOL/USDT 1m Data")
    print("=" * 80)
    
    # Load config
    try:
        config_path = Path("config.yaml")
        if not config_path.exists():
            logger.info("config.yaml not found, using defaults from config.yaml.example")
            config_path = Path("config.yaml.example")
        
        config = load_config(str(config_path))
        scalping_cfg = config.scalping
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)
    
    # Check for data file
    data_file = Path("icarus/cache/SOL_USDT_1m_30d.csv")
    if not data_file.exists():
        logger.error(f"Data file not found: {data_file}")
        sys.exit(1)
    
    logger.info(f"✓ Loaded config from {config_path}")
    logger.info(f"✓ Found data file: {data_file}")
    
    # Initialize backtest engine
    engine = BacktestEngine(scalping_cfg)
    logger.info("✓ Backtest engine initialized")
    
    # Run backtest
    print("\n" + "─" * 80)
    print("Running Backtest...")
    print("─" * 80 + "\n")
    
    result = engine.run(
        csv_path=data_file,
        symbol="SOL/USDT",
        initial_balance=100.0,  # $100 initial
    )
    
    # Display results
    print("\n" + "=" * 80)
    print(f"BACKTEST RESULTS - {result.symbol}")
    print("=" * 80)
    
    print(f"\nConfiguration:")
    print(f"  Initial Balance:      ${100.0:.2f}")
    print(f"  Test Duration:        {result.elapsed_seconds:.1f}s")
    print(f"  Candles Processed:    (from CSV)")
    
    print(f"\nStrategy Parameters:")
    print(f"  TP1:                  {scalping_cfg.tp1_percent*100:.2f}%")
    print(f"  TP2:                  {scalping_cfg.tp2_percent*100:.2f}%")
    print(f"  SL:                   {scalping_cfg.fixed_sl_percent*100:.2f}%")
    print(f"  TP1 Fraction:         {scalping_cfg.tp1_fraction*100:.0f}%")
    print(f"  Risk/Trade:           {scalping_cfg.risk_per_trade*100:.2f}%")
    
    if result.metrics:
        print(f"\nPerformance Metrics:")
        print(format_metrics(result.metrics))
    
    if result.trades:
        print(f"\n\nRecent Trades (last 5):")
        print("─" * 80)
        for trade in result.trades[-5:]:
            direction_str = trade['direction'] if isinstance(trade['direction'], str) else trade['direction'].value
            direction = "LONG" if direction_str == 'long' else "SHORT"
            print(f"  {trade['entry_time'][:19]} | {direction} @ ${trade['entry_price']:.4f} → ${trade['exit_price']:.4f} | "
                  f"PnL: ${trade['pnl_usd']:+.2f} ({trade['pnl_percent']:+.1f}%)")
    else:
        print(f"\n❌ No completed trades during backtest.")
    
    print("\n" + "=" * 80)
    
    if result.metrics.get('total_return_percent', 0) > 0:
        print(f"✅ PROFITABLE: +{result.metrics['total_return_percent']:.2f}% return")
    else:
        print(f"❌ LOSS: {result.metrics['total_return_percent']:.2f}% return")
    
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
