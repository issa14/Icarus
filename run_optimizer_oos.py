
import argparse
import pandas as pd
from icarus.backtest.futures_engine import FuturesBacktestEngine
from icarus.backtest.metrics import BacktestMetrics
from icarus.config.loader import ConfigLoader
from datetime import datetime, timedelta

def run_optimizer_oos(futures_mode: bool, leverage_values: list):
    # Load configuration
    config_loader = ConfigLoader("config.yaml")
    config = config_loader.load_config()

    # 1. Load the complete CSV
    # Assuming the data path is configured in config.yaml or can be passed as an argument
    data_path = "icarus/cache/SOL_USDT_1m_30d.csv" # This should be dynamic or configured
    full_data = pd.read_csv(data_path)
    full_data['timestamp'] = pd.to_datetime(full_data['timestamp'], unit='s')

    # Define IS and OOS periods
    # Assuming 30 days of data and 1m candles, first 20 days for IS, last 10 for OOS
    end_is_date = full_data['timestamp'].min() + timedelta(days=20)
    is_data = full_data[full_data['timestamp'] <= end_is_date]
    oos_data = full_data[full_data['timestamp'] > end_is_date]

    print(f"IS data range: {is_data['timestamp'].min()} to {is_data['timestamp'].max()}")
    print(f"OOS data range: {oos_data['timestamp'].min()} to {oos_data['timestamp'].max()}")

    # Placeholder for grid search parameters
    # In a real scenario, these would be defined or loaded
    grid_search_params = {
        'TP1_values': [0.005, 0.006, 0.007],
        'TP2_values': [0.009, 0.010, 0.011],
        'SL_values': [0.003, 0.004, 0.005],
        'TP1_fraction_values': [0.5, 0.6, 0.7],
        'kelly_fraction_values': [0.1, 0.15, 0.2]
    }

    best_is_results = []

    # 2. Perform grid search ONLY on the first 20 days (IS)
    for leverage in leverage_values:
        for tp1 in grid_search_params['TP1_values']:
            for tp2 in grid_search_params['TP2_values']:
                for sl in grid_search_params['SL_values']:
                    for tp1_fraction in grid_search_params['TP1_fraction_values']:
                        for kelly_fraction in grid_search_params['kelly_fraction_values']:
                            print(f"Running IS backtest for Leverage:{leverage}, TP1:{tp1}, TP2:{tp2}, SL:{sl}, TP1_Fraction:{tp1_fraction}, Kelly_Fraction:{kelly_fraction}")

                            # Update config with current params
                            config.strategy.params.TP1 = tp1
                            config.strategy.params.TP2 = tp2
                            config.strategy.params.SL = sl
                            config.strategy.params.TP1_fraction = tp1_fraction
                            config.strategy.params.kelly_fraction = kelly_fraction
                            config.futures.leverage = leverage

                            engine = FuturesBacktestEngine(config=config, data=is_data)
                            engine.run()
                            
                            metrics = BacktestMetrics(engine.trade_logger.trades, initial_balance=config.broker.initial_balance)
                            sharpe_ratio = metrics.sharpe_ratio()

                            if sharpe_ratio is not None:
                                best_is_results.append({
                                    'leverage': leverage,
                                    'TP1': tp1, 'TP2': tp2, 'SL': sl,
                                    'TP1_fraction': tp1_fraction,
                                    'kelly_fraction': kelly_fraction,
                                    'sharpe_is': sharpe_ratio,
                                    'return_is': metrics.total_return(),
                                    'drawdown_is': metrics.max_drawdown()
                                })

    # Sort and take top 5 (or fewer if less than 5 results)
    best_is_results = sorted(best_is_results, key=lambda x: x['sharpe_is'], reverse=True)[:5]

    print("\n🏆 TOP {len(best_is_results)} — VALIDATION OUT-OF-SAMPLE")
    final_oos_results = []

    # 4. Re-run top combinations on OOS data
    for i, params in enumerate(best_is_results):
        print(f"Re-running combination #{i+1} on OOS data (Sharpe IS: {params['sharpe_is']:.2f})...")

        config.strategy.params.TP1 = params['TP1']
        config.strategy.params.TP2 = params['TP2']
        config.strategy.params.SL = params['SL']
        config.strategy.params.TP1_fraction = params['TP1_fraction']
        config.strategy.params.kelly_fraction = params['kelly_fraction']
        config.futures.leverage = params['leverage']

        engine_oos = FuturesBacktestEngine(config=config, data=oos_data)
        engine_oos.run()

        metrics_oos = BacktestMetrics(engine_oos.trade_logger.trades, initial_balance=config.broker.initial_balance)
        sharpe_oos = metrics_oos.sharpe_ratio()
        return_oos = metrics_oos.total_return()
        drawdown_oos = metrics_oos.max_drawdown()

        final_oos_results.append({
            **params,
            'sharpe_oos': sharpe_oos,
            'return_oos': return_oos,
            'drawdown_oos': drawdown_oos
        })

    # 5. Compare IS vs OOS metrics
    for i, result in enumerate(final_oos_results):
        sharpe_is = result['sharpe_is']
        sharpe_oos = result['sharpe_oos']
        return_oos = result['return_oos']
        drawdown_oos = result['drawdown_oos']

        status = ""
        if sharpe_oos is not None and sharpe_is is not None:
            if sharpe_oos >= 0.8 * sharpe_is and return_oos > 0: # Basic non-overfitting check
                status = "✅"
            elif return_oos <= 0:
                status = "❌❌ (négatif)"
            else:
                status = "❌ overfit"
        else:
            status = "⚠️ (metrics unavailable)"


        sharpe_drop_percent = ((sharpe_is - sharpe_oos) / sharpe_is * 100) if sharpe_is else 0

        print(f"#{i+1} Score IS: {sharpe_is:.2f} → Score OOS: {sharpe_oos:.2f} (drop: {sharpe_drop_percent:.0f}%) {status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Out-of-Sample Optimizer for Futures.")
    parser.add_argument("--futures", action="store_true", help="Run in futures mode.")
    parser.add_argument("--leverage", type=str, default="3,5,10", help="Comma-separated leverage values (e.g., '3,5,10').")
    
    args = parser.parse_args()

    leverage_values = [int(x) for x in args.leverage.split(',')]

    run_optimizer_oos(args.futures, leverage_values)
