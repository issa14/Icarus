#!/usr/bin/env python3
"""Final validation script for Futures migration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

print("\n" + "=" * 70)
print("ICARUS FUTURES MIGRATION - FINAL VALIDATION")
print("=" * 70)

def check_file_exists(path: str) -> bool:
    """Check if file exists."""
    exists = Path(path).exists()
    status = "✅" if exists else "❌"
    print(f"{status} {path}")
    return exists

def check_import(module: str, name: str) -> bool:
    """Check if import works."""
    try:
        mod = __import__(module, fromlist=[name])
        getattr(mod, name)
        print(f"✅ from {module} import {name}")
        return True
    except Exception as e:
        print(f"❌ from {module} import {name} → {e}")
        return False

print("\n[1] Configuration Files")
print("-" * 70)
all_ok = True
all_ok &= check_file_exists("config.yaml.example")
all_ok &= check_file_exists("icarus/config/models.py")

print("\n[2] Execution Controllers")
print("-" * 70)
all_ok &= check_file_exists("icarus/execution/engine.py")
all_ok &= check_file_exists("icarus/execution/futures.py")
all_ok &= check_import("icarus.execution", "SpotExecutionController")
all_ok &= check_import("icarus.execution", "FuturesExecutionController")

print("\n[3] Risk Controllers")
print("-" * 70)
all_ok &= check_file_exists("icarus/risk/engine.py")
all_ok &= check_file_exists("icarus/risk/futures.py")
all_ok &= check_import("icarus.risk", "SpotRiskController")
all_ok &= check_import("icarus.risk", "FuturesRiskController")

print("\n[4] Types & Interfaces")
print("-" * 70)
all_ok &= check_file_exists("icarus/core/types.py")
all_ok &= check_import("icarus.core.types", "OrderRequest")
all_ok &= check_import("icarus.core.types", "PositionSize")

print("\n[5] Orchestrator")
print("-" * 70)
all_ok &= check_file_exists("icarus/orchestrator.py")
all_ok &= check_import("icarus.orchestrator", "Orchestrator")

print("\n[6] Factory Pattern Verification")
print("-" * 70)
try:
    from icarus.config.models import ScalpingConfig, ExchangeConfig
    from icarus.orchestrator import Orchestrator
    import tempfile
    
    # Create temp config for Spot
    spot_config = """
scalping:
  symbols: [SOL/USDT]
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
  leverage: 1
  margin_mode: isolated
  hedge_mode: false
  cooldown_seconds: 120
  fee_maker: 0.0002
  fee_taker: 0.0004
exchange:
  exchange: binance
  api_key: test
  api_secret: test
  sandbox: true
  futures: false
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
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(spot_config)
        spot_path = f.name
    
    orch_spot = Orchestrator(config_path=spot_path)
    orch_spot._init_components()
    
    from icarus.execution import SpotExecutionController
    from icarus.risk import SpotRiskController
    
    if isinstance(orch_spot._execution, SpotExecutionController):
        print("✅ Spot factory: SpotExecutionController selected")
    else:
        print(f"❌ Spot factory: Expected SpotExecutionController, got {type(orch_spot._execution).__name__}")
        all_ok = False
    
    if isinstance(orch_spot._risk, SpotRiskController):
        print("✅ Spot factory: SpotRiskController selected")
    else:
        print(f"❌ Spot factory: Expected SpotRiskController, got {type(orch_spot._risk).__name__}")
        all_ok = False
    
    Path(spot_path).unlink()
    
    # Create temp config for Futures
    futures_config = spot_config.replace("futures: false", "futures: true").replace("leverage: 1", "leverage: 5")
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(futures_config)
        futures_path = f.name
    
    orch_futures = Orchestrator(config_path=futures_path)
    orch_futures._init_components()
    
    from icarus.execution import FuturesExecutionController
    from icarus.risk import FuturesRiskController
    
    if isinstance(orch_futures._execution, FuturesExecutionController):
        print("✅ Futures factory: FuturesExecutionController selected")
    else:
        print(f"❌ Futures factory: Expected FuturesExecutionController, got {type(orch_futures._execution).__name__}")
        all_ok = False
    
    if isinstance(orch_futures._risk, FuturesRiskController):
        print("✅ Futures factory: FuturesRiskController selected")
    else:
        print(f"❌ Futures factory: Expected FuturesRiskController, got {type(orch_futures._risk).__name__}")
        all_ok = False
    
    Path(futures_path).unlink()
    
except Exception as e:
    print(f"❌ Factory pattern test failed: {e}")
    import traceback
    traceback.print_exc()
    all_ok = False

print("\n[7] Test Files")
print("-" * 70)
all_ok &= check_file_exists("test_futures_factory.py")
all_ok &= check_file_exists("test_orchestrator_integration.py")

print("\n[8] Documentation")
print("-" * 70)
all_ok &= check_file_exists("MIGRATION_FUTURES.md")

print("\n" + "=" * 70)
if all_ok:
    print("✅ MIGRATION VALIDATION PASSED - All components present and working!")
    print("=" * 70)
    print("\n🚀 Ready to use:")
    print("   • Spot mode: python main.py (default)")
    print("   • Futures mode: Set exchange.futures=true in config.yaml")
    sys.exit(0)
else:
    print("❌ MIGRATION VALIDATION FAILED - Some components missing")
    print("=" * 70)
    sys.exit(1)
