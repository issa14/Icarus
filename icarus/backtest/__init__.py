"""icarus.backtest — Backtesting framework for Icarus strategies.

Exports:
    BacktestEngine            — Spot backtest engine
    BacktestResult            — Spot backtest result
    FuturesBacktestEngine     — Futures backtest engine (leverage, margin, liquidation)
    FuturesBacktestResult     — Futures backtest result
    compute_metrics           — Pure metrics computation
    optimizer_score           — Composite scoring function
    grid_search_spot          — Grid search for spot mode
    grid_search_futures       — Grid search for futures mode
"""

from icarus.backtest.engine import BacktestEngine, BacktestResult
from icarus.backtest.futures_engine import FuturesBacktestEngine, FuturesBacktestResult
from icarus.backtest.metrics import compute_metrics
from icarus.backtest.optimizer import (
    grid_search_spot,
    grid_search_futures,
    optimizer_score,
    optimizer_score_futures,
)

# Backward-compatible alias
grid_search = grid_search_spot

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "FuturesBacktestEngine",
    "FuturesBacktestResult",
    "compute_metrics",
    "grid_search",
    "grid_search_spot",
    "grid_search_futures",
    "optimizer_score",
    "optimizer_score_futures",
]