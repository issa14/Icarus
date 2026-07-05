#!/usr/bin/env python3
"""Test script for Futures factory pattern."""

import asyncio
import sys
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from icarus.config.models import ScalpingConfig, ExchangeConfig
from icarus.execution import SpotExecutionController, FuturesExecutionController
from icarus.risk import SpotRiskController, FuturesRiskController


def test_imports():
    """Test that all classes are importable."""
    logger.info("✓ All classes imported successfully")
    print(f"  SpotExecutionController: {SpotExecutionController}")
    print(f"  FuturesExecutionController: {FuturesExecutionController}")
    print(f"  SpotRiskController: {SpotRiskController}")
    print(f"  FuturesRiskController: {FuturesRiskController}")
    return True


def test_factory_spot():
    """Test factory instantiation for Spot mode."""
    logger.info("\n=== Testing Spot Mode Factory ===")
    
    cfg = ScalpingConfig(
        symbol="SOL/USDT",
        timeframe="1m",
        Kelly_percentage=0.25,
        max_positions=3,
        max_daily_loss_percent=0.05,  # 5% as decimal
        trailing_activation_ratio=0.7,
        trailing_callback_ratio=0.5,
        tp1_percent=0.005,  # 0.5%
        tp2_percent=0.010,  # 1.0%, must be > tp1_percent
        fixed_sl_percent=0.003,  # Must be < tp1_percent
    )
    
    exchange_cfg = ExchangeConfig(
        exchange="binance",
        api_key="test_key",
        secret="test_secret",
        futures=False
    )
    
    try:
        risk = SpotRiskController(config=cfg, db_path=":memory:")
        execution = SpotExecutionController(config=cfg, exchange_cfg=exchange_cfg)
        logger.info(f"✓ Spot controllers instantiated")
        print(f"  Risk: {type(risk).__name__}")
        print(f"  Execution: {type(execution).__name__}")
        return True
    except Exception as e:
        logger.error(f"✗ Failed to instantiate Spot controllers: {e}")
        return False


def test_factory_futures():
    """Test factory instantiation for Futures mode."""
    logger.info("\n=== Testing Futures Mode Factory ===")
    
    cfg = ScalpingConfig(
        symbol="SOL/USDT",
        timeframe="1m",
        Kelly_percentage=0.25,
        max_positions=3,
        max_daily_loss_percent=0.05,  # 5% as decimal
        trailing_activation_ratio=0.7,
        trailing_callback_ratio=0.5,
        tp1_percent=0.005,  # 0.5%
        tp2_percent=0.010,  # 1.0%, must be > tp1_percent
        fixed_sl_percent=0.003,  # Must be < tp1_percent
        leverage=5,
        margin_mode="cross",
        hedge_mode=False,
    )
    
    exchange_cfg = ExchangeConfig(
        exchange="binance",
        api_key="test_key",
        secret="test_secret",
        futures=True
    )
    
    try:
        risk = FuturesRiskController(config=cfg, db_path=":memory:")
        execution = FuturesExecutionController(config=cfg, exchange_cfg=exchange_cfg)
        logger.info(f"✓ Futures controllers instantiated")
        print(f"  Risk: {type(risk).__name__}")
        print(f"  Execution: {type(execution).__name__}")
        return True
    except Exception as e:
        logger.error(f"✗ Failed to instantiate Futures controllers: {e}")
        return False


def test_config_validation():
    """Test configuration validation."""
    logger.info("\n=== Testing Config Validation ===")
    
    try:
        # Valid Futures config
        cfg = ScalpingConfig(
            symbol="BTC/USDT",
            timeframe="1m",
            Kelly_percentage=0.25,
            max_positions=3,
            max_daily_loss_percent=0.05,  # 5% as decimal
            trailing_activation_ratio=0.7,
            trailing_callback_ratio=0.5,
            tp1_percent=0.005,  # 0.5%
            tp2_percent=0.010,  # 1.0%, must be > tp1_percent
            fixed_sl_percent=0.003,  # Must be < tp1_percent
            leverage=10,
            margin_mode="isolated",
            hedge_mode=False,
        )
        logger.info("✓ Valid Futures config accepted")
        
        # Test invalid leverage (should fail)
        try:
            bad_cfg = ScalpingConfig(
                symbol="BTC/USDT",
                timeframe="1m",
                Kelly_percentage=0.25,
                max_positions=3,
                max_daily_loss_percent=0.05,  # 5% as decimal
                trailing_activation_ratio=0.7,
                trailing_callback_ratio=0.5,
                tp1_percent=0.005,  # 0.5%
                tp2_percent=0.010,  # 1.0%, must be > tp1_percent
                fixed_sl_percent=0.003,  # Must be < tp1_percent
                leverage=150,  # Invalid: > 125
                margin_mode="isolated",
                hedge_mode=False,
            )
            logger.warning("✗ Invalid leverage not caught")
            return False
        except Exception as e:
            logger.info(f"✓ Invalid leverage correctly rejected: {type(e).__name__}")
        
        return True
    except Exception as e:
        logger.error(f"✗ Config validation failed: {e}")
        return False


def main():
    """Run all tests."""
    logger.info("=" * 50)
    logger.info("Testing Futures Factory Implementation")
    logger.info("=" * 50)
    
    results = {
        "Imports": test_imports(),
        "Config Validation": test_config_validation(),
        "Spot Factory": test_factory_spot(),
        "Futures Factory": test_factory_futures(),
    }
    
    logger.info("\n" + "=" * 50)
    logger.info("Test Summary")
    logger.info("=" * 50)
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(results.values())
    exit_code = 0 if all_passed else 1
    logger.info(f"\nOverall: {'✓ ALL TESTS PASSED' if all_passed else '✗ SOME TESTS FAILED'}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
