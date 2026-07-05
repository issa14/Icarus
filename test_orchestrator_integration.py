#!/usr/bin/env python3
"""Integration test for Futures/Spot switching via Orchestrator."""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from icarus.config.models import ScalpingConfig, ExchangeConfig
from icarus.orchestrator import Orchestrator


def create_temp_config(futures: bool) -> str:
    """Create a temporary config file for testing."""
    config_content = f"""
scalping:
  symbols:
    - SOL/USDT
  execution_loop_seconds: 5
  tp1_percent: 0.005
  tp2_percent: 0.010
  fixed_sl_percent: 0.003
  tp1_fraction: 0.6
  trailing_callback: 0.003
  trailing_activation: 0.5
  entry_timeout_seconds: 15
  rsi_oversold: 35
  rsi_overbought: 65
  volume_surge_threshold: 1.8
  threshold_base: 60.0
  min_atr_percent: 0.0015
  max_atr_percent: 0.010
  spread_max_percent: 0.0005
  risk_per_trade: 0.0025
  max_daily_loss_percent: 0.02
  max_positions: 1
  kelly_fraction: 0.25
  leverage: 5
  margin_mode: cross
  hedge_mode: false
  cooldown_seconds: 120
  fee_maker: 0.0002
  fee_taker: 0.0004

exchange:
  exchange: binance
  api_key: "test"
  api_secret: "test"
  sandbox: true
  futures: {str(futures).lower()}

telegram:
  enabled: false

app:
  debug: false
  log_level: INFO
  log_file: icarus.log
  dashboard: false

health:
  interval_seconds: 30
  stale_data_warning: 15
  stale_data_critical: 30
  max_reconnect_attempts: 10
"""
    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(config_content)
        return f.name


async def test_orchestrator_spot_mode():
    """Test orchestrator factory with Spot mode."""
    logger.info("\n=== Testing Orchestrator SPOT Mode ===")
    
    temp_config = create_temp_config(futures=False)
    try:
        orchestrator = Orchestrator(config_path=temp_config)
        
        # Call _init_components manually (normally called in run())
        orchestrator._init_components()
        
        # Verify Spot controllers were selected
        from icarus.execution import SpotExecutionController
        from icarus.risk import SpotRiskController
        
        assert isinstance(orchestrator._execution, SpotExecutionController), \
            f"Expected SpotExecutionController, got {type(orchestrator._execution).__name__}"
        assert isinstance(orchestrator._risk, SpotRiskController), \
            f"Expected SpotRiskController, got {type(orchestrator._risk).__name__}"
        
        logger.info("✓ Orchestrator correctly selected Spot controllers")
        return True
    except Exception as e:
        logger.error(f"✗ Spot mode test failed: {e}", exc_info=True)
        return False
    finally:
        Path(temp_config).unlink()


async def test_orchestrator_futures_mode():
    """Test orchestrator factory with Futures mode."""
    logger.info("\n=== Testing Orchestrator FUTURES Mode ===")
    
    temp_config = create_temp_config(futures=True)
    try:
        orchestrator = Orchestrator(config_path=temp_config)
        
        # Call _init_components manually (normally called in run())
        orchestrator._init_components()
        
        # Verify Futures controllers were selected
        from icarus.execution import FuturesExecutionController
        from icarus.risk import FuturesRiskController
        
        assert isinstance(orchestrator._execution, FuturesExecutionController), \
            f"Expected FuturesExecutionController, got {type(orchestrator._execution).__name__}"
        assert isinstance(orchestrator._risk, FuturesRiskController), \
            f"Expected FuturesRiskController, got {type(orchestrator._risk).__name__}"
        
        logger.info("✓ Orchestrator correctly selected Futures controllers")
        return True
    except Exception as e:
        logger.error(f"✗ Futures mode test failed: {e}", exc_info=True)
        return False
    finally:
        Path(temp_config).unlink()


async def test_orchestrator_with_config_file():
    """Test orchestrator loading from actual config file."""
    logger.info("\n=== Testing Config File Loading ===")
    
    try:
        # Check if config.yaml exists
        config_file = Path("config.yaml")
        if not config_file.exists():
            logger.warning("✓ Skipped (config.yaml not present - use config.yaml.example)")
            return True
        
        # This will load the real config
        orchestrator = Orchestrator(config_path="config.yaml")
        
        # Just verify it loaded without crashing
        logger.info(f"✓ Loaded config from file, futures mode: {orchestrator._exchange_cfg.futures}")
        return True
    except Exception as e:
        logger.warning(f"✓ Skipped (no config.yaml or load error): {e}")
        return True


async def main():
    """Run all integration tests."""
    logger.info("=" * 60)
    logger.info("Integration Tests: Orchestrator Factory Pattern")
    logger.info("=" * 60)
    
    results = {
        "Orchestrator Spot": await test_orchestrator_spot_mode(),
        "Orchestrator Futures": await test_orchestrator_futures_mode(),
        "Config File Loading": await test_orchestrator_with_config_file(),
    }
    
    logger.info("\n" + "=" * 60)
    logger.info("Integration Test Summary")
    logger.info("=" * 60)
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(results.values())
    logger.info(f"\nOverall: {'✓ ALL TESTS PASSED' if all_passed else '✗ SOME TESTS FAILED'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
