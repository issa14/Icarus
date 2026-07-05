"""icarus.backtest.metrics — Métriques de performance pour le backtesting.

Fonctions pures : prennent une liste de trades, retournent des métriques.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np


def compute_metrics(
    trades: list[dict],
    initial_balance: float = 100.0,
    risk_free_rate: float = 0.0,
) -> Dict:
    """Calcule toutes les métriques de performance à partir d'une liste de trades.

    Parameters
    ----------
    trades: list[dict]
        Chaque trade doit avoir: ``pnl_usd``, ``is_winner``, ``entry_time``.
    initial_balance: float
        Capital initial.
    risk_free_rate: float
        Taux sans risque annuel (ex: 0.05 = 5%).

    Returns
    -------
    dict avec toutes les métriques.
    """
    if not trades:
        return _empty_metrics()

    n_trades = len(trades)
    wins = sum(1 for t in trades if t["is_winner"])
    losses = n_trades - wins
    win_rate = wins / max(n_trades, 1)

    # PnL
    pnl_list = [t["pnl_usd"] for t in trades]
    total_pnl = sum(pnl_list)
    total_return_pct = total_pnl / initial_balance * 100.0

    # Win/Loss sizes
    win_pnls = [p for t, p in zip(trades, pnl_list) if t["is_winner"]]
    loss_pnls = [p for t, p in zip(trades, pnl_list) if not t["is_winner"]]

    avg_win = float(np.mean(win_pnls)) if win_pnls else 0.0
    avg_loss = float(np.mean(loss_pnls)) if loss_pnls else 0.0

    # Payoff ratio
    payoff_ratio = abs(avg_win / avg_loss) if abs(avg_loss) > 1e-9 else 0.0

    # Profit Factor
    gross_profit = sum(p for p in win_pnls)
    gross_loss = abs(sum(p for p in loss_pnls))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Expectancy
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))

    # Equity curve
    equity = _build_equity_curve(trades, initial_balance)

    # Max Drawdown
    max_dd_pct, max_dd_usd = _max_drawdown(equity)

    # Sharpe Ratio (annualisé)
    sharpe = _sharpe_ratio(equity, risk_free_rate)

    # Calmar Ratio
    calmar = total_return_pct / abs(max_dd_pct) if abs(max_dd_pct) > 1e-9 else 0.0

    # Consecutive stats
    max_consec_wins = _max_consecutive(trades, is_win=True)
    max_consec_losses = _max_consecutive(trades, is_win=False)

    # Duration
    if len(trades) >= 2:
        first = min(t["entry_time"] for t in trades)
        last = max(t["entry_time"] for t in trades)
        duration_days = (last - first) / 86_400.0
    else:
        duration_days = 1.0

    # Trades per day
    trades_per_day = n_trades / max(duration_days, 1.0)

    # Kelly optimal
    if abs(avg_loss) <= 1e-9 or abs(avg_win) <= 1e-9:
        kelly = 0.0
    else:
        payoff = avg_win / abs(avg_loss)
        if abs(payoff) <= 1e-9:
            kelly = 0.0
        else:
            kelly = win_rate - ((1 - win_rate) / payoff)

    return {
        "total_trades": n_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate * 100, 1),
        "total_pnl_usd": round(total_pnl, 3),
        "total_return_pct": round(total_return_pct, 2),
        "avg_win_usd": round(avg_win, 4),
        "avg_loss_usd": round(avg_loss, 4),
        "payoff_ratio": round(payoff_ratio, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_usd": round(expectancy, 4),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_drawdown_usd": round(max_dd_usd, 3),
        "sharpe_ratio": round(sharpe, 2),
        "calmar_ratio": round(calmar, 2),
        "kelly_optimal": round(kelly, 2),
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
        "trades_per_day": round(trades_per_day, 1),
        "duration_days": round(duration_days, 1),
        "final_balance": round(equity[-1]["equity"], 3),
    }


def _build_equity_curve(trades: list[dict], initial: float) -> list[dict]:
    """Construit la courbe d'equity point par point."""
    curve = [{"trade_idx": 0, "equity": initial}]
    running = initial
    for i, t in enumerate(trades):
        running += t["pnl_usd"]
        curve.append({"trade_idx": i + 1, "equity": running})
    return curve


def _max_drawdown(equity: list[dict]) -> Tuple[float, float]:
    """Calcule le max drawdown en % et en USD."""
    values = np.array([e["equity"] for e in equity])
    peak = np.maximum.accumulate(values)
    dd = (values - peak) / peak * 100.0
    max_dd_pct = float(np.min(dd))
    max_dd_idx = int(np.argmin(dd))
    max_dd_usd = float(values[max_dd_idx] - peak[max_dd_idx])
    return max_dd_pct, max_dd_usd


def _sharpe_ratio(equity: list[dict], risk_free: float = 0.0) -> float:
    """Sharpe ratio annualisé basé sur les variations quotidiennes."""
    values = np.array([e["equity"] for e in equity])
    if len(values) < 2:
        return 0.0

    returns = np.diff(values) / values[:-1]
    if len(returns) < 2:
        return 0.0

    # Si moins de 20 points, on annualise de façon simplifiée
    mean_ret = float(np.mean(returns))
    std_ret = float(np.std(returns, ddof=1))

    if std_ret < 1e-9:
        return 0.0

    # Annualisation : scalping 1m, ~1440 bougies/jour, ~365 jours
    # Mais on a trade-par-trade, donc on annualise sur le nombre de trades
    sharpe = (mean_ret - risk_free / 365) / std_ret
    # Annualisation grossière (sqrt de 252 jours de trading)
    sharpe_annual = sharpe * math.sqrt(max(len(returns), 1))

    return sharpe_annual


def _max_consecutive(trades: list[dict], is_win: bool = True) -> int:
    """Calcule la plus longue séquence de wins ou losses consécutifs."""
    max_seq = 0
    current = 0
    for t in trades:
        if t["is_winner"] == is_win:
            current += 1
            max_seq = max(max_seq, current)
        else:
            current = 0
    return max_seq


def _empty_metrics() -> Dict:
    """Métriques pour un résultat vide."""
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "total_pnl_usd": 0.0,
        "total_return_pct": 0.0,
        "avg_win_usd": 0.0,
        "avg_loss_usd": 0.0,
        "payoff_ratio": 0.0,
        "profit_factor": 0.0,
        "expectancy_usd": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_usd": 0.0,
        "sharpe_ratio": 0.0,
        "calmar_ratio": 0.0,
        "kelly_optimal": 0.0,
        "max_consec_wins": 0,
        "max_consec_losses": 0,
        "trades_per_day": 0.0,
        "duration_days": 0.0,
        "final_balance": 100.0,
    }