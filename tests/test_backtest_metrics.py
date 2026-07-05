from icarus.backtest.metrics import compute_metrics


def test_compute_metrics_without_losses_handles_zero_division() -> None:
    trades = [
        {"pnl_usd": 1.25, "is_winner": True, "entry_time": 1_000}
    ]

    metrics = compute_metrics(trades, initial_balance=100.0)

    assert metrics["total_trades"] == 1
    assert metrics["wins"] == 1
    assert metrics["losses"] == 0
    assert metrics["win_rate"] == 100.0
    assert metrics["final_balance"] == 101.25
    assert metrics["kelly_optimal"] >= 0.0
