#!/usr/bin/env python3
"""
ICARUS FUTURES MIGRATION - QUICK START GUIDE
═════════════════════════════════════════════════════════════════════════

Migration Status: ✅ COMPLETE & VALIDATED
Date: 2026-07-04
Mode: Production Ready
Updated: 2026-07-05 — Demo trading support for Binance futures
"""

print("""
╔═════════════════════════════════════════════════════════════════════════╗
║                                                                         ║
║           🤖 ICARUS FUTURES MIGRATION - COMPLETE ✅                    ║
║                                                                         ║
║  Your trading bot now supports BOTH Spot AND Futures markets!          ║
║                                                                         ║
╚═════════════════════════════════════════════════════════════════════════╝

📋 WHAT'S NEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✨ NEW Components:
  • FuturesExecutionController   (icarus/execution/futures.py)
  • FuturesRiskController        (icarus/risk/futures.py)

✏️  RENAMED Components:
  • ExecutionController → SpotExecutionController
  • RiskController → SpotRiskController

🔧 NEW Config Options:
  • exchange.futures: bool (default: false)
  • scalping.leverage: 1-125 (default: 1)
  • scalping.margin_mode: "cross" | "isolated" (default: "isolated")
  • scalping.hedge_mode: bool (default: false)

🏭 NEW Architecture:
  • Factory Pattern: Orchestrator auto-selects Spot or Futures controller
  • Backward Compatibility: Old code still works with aliases

🆕  2026-07-05: Demo Trading Binance Futures
  • Binance testnet/sandbox is DEPRECATED for futures
  • The bot now auto-switches to DEMO TRADING (demo-fapi.binance.com)
  • Set sandbox: true + futures: true → demo trading is activated
  • Works with your Binance API credentials on the demo network


🚀 QUICK START
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣  RUN IN SPOT MODE (Default)
─────────────────────────────────

   Just use the bot normally:

   $ python main.py

   Or explicitly:

   $ python main.py --config config.yaml

   (If you don't change config.yaml, it stays in Spot mode)


2️⃣  SWITCH TO FUTURES MODE
─────────────────────────────────

   Step 1: Generate config template (if needed)
   $ python main.py --generate-config config.yaml

   Step 2: Edit config.yaml
   $ vim config.yaml

   Look for this section:

   ┌─────────────────────────────────────────┐
   │ exchange:                               │
   │   exchange: binance                     │
   │   api_key: "YOUR_API_KEY"              │
   │   api_secret: "YOUR_API_SECRET"        │
   │   sandbox: true    ← demo trading      │
   │   futures: false    ← CHANGE TO: true  │
   │                                         │
   │ scalping:                               │
   │   ...                                   │
   │   leverage: 1     ← CHANGE (e.g.: 5)   │
   │   margin_mode: isolated                 │
   │   hedge_mode: false                     │
   └─────────────────────────────────────────┘

   ⚠️  IMPORTANT: Binance testnet/sandbox is DEPRECATED for futures.
       When sandbox: true + futures: true, the bot automatically
       uses DEMO TRADING (demo-fapi.binance.com) instead of the
       old testnet.  No code changes needed — just set both flags!

   Step 3: Restart bot
   $ python main.py --config config.yaml

   The bot will now trade Futures on the demo network! 🎯

   Check logs to confirm:
   - "[FuturesExecutionController] Demo trading Binance activé..."
   - "[FuturesExecutionController] SOL/USDT: leverage=1, margin_mode=isolated"


💡 KEY DIFFERENCES: Spot vs Futures
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SPOT MODE:
  ✓ Buy actual coins
  ✓ No leverage
  ✓ Simple: buy low, sell high
  ✓ Slower (overnight holds possible)
  ✓ Use for: Long-term HODLing

FUTURES MODE:
  ✓ Trade contracts (no actual coins)
  ✓ Up to 125x leverage (configure wisely!)
  ✓ Long AND Short trades possible
  ✓ Fast: close in minutes/seconds (scalping perfect)
  ✓ Auto-liquidation if price moves against you
  ✓ Use for: Intraday scalping (like Icarus was designed for!)


⚠️  SAFETY CHECKLIST FOR FUTURES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before going LIVE with leverage, check:

  [ ] Start with demo trading (sandbox: true + futures: true)
  [ ] ⚠️  Binance testnet/sandbox is DEPRECATED for futures
        → The bot auto-switches to demo-fapi.binance.com
  [ ] Use SMALL leverage first (e.g., 2x or 3x, not 125x!)
  [ ] Test with paper trading first
  [ ] Understand liquidation price calculation
  [ ] Verify stop-loss distance from liquidation
  [ ] Monitor daily loss % (circuit breaker at max_daily_loss_percent)
  [ ] Check cross vs isolated margin mode implications
  [ ] Verify API key has futures trading permission
  [ ] Have an exit plan for each trade


📊 MONITOR YOUR BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

While bot is running, check logs:

  $ tail -f icarus.log

  Look for messages like:
  - "[FuturesExecutionController] Demo trading Binance activé..."
  - "[FuturesExecutionController] Exchange binance initialisé en futures."
  - "[FuturesRiskController] Signal validé..."

  These confirm Futures mode is active!


🔍 TESTING & VALIDATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Run validation to confirm everything works:

  $ python validate_migration.py

Run unit tests:

  $ python test_futures_factory.py

Run integration tests:

  $ python test_orchestrator_integration.py


📚 DETAILED DOCUMENTATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Full migration details:

  $ cat MIGRATION_FUTURES.md


❓ FAQ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Q: Can I run Spot and Futures bots at the same time?
A: Yes! Run two instances with different config files:
   Terminal 1: python main.py --config config_spot.yaml
   Terminal 2: python main.py --config config_futures.yaml

Q: What if I set leverage too high?
A: The bot will liquidate if price moves against you.
   Start small (2-3x), test thoroughly on demo first.

Q: How do I know if Futures mode is active?
A: Check logs:
   • Spot: "[ExecutionController] Exchange..."
   • Futures: "[FuturesExecutionController] Exchange... initialisé en futures."
   • Futures Demo: "[FuturesExecutionController] Demo trading Binance activé..."

Q: Can I switch back to Spot?
A: Yes! Just set exchange.futures: false and restart.

Q: What about Binance testnet/sandbox for futures?
A: Binance deprecated the old testnet for futures. Icarus now uses the
   new DEMO TRADING network (demo-fapi.binance.com) automatically when
   sandbox: true + futures: true. No configuration changes needed!

Q: Do I need special API keys for demo trading?
A: No — use the same Binance API keys. The demo network is isolated
   from the real market, so your real funds are never at risk.

Q: What's the risk management?
A: • Daily loss limit (circuit breaker)
   • Kelly fraction sizing
   • Liquidation distance check
   • Position size cap (max_positions)


✅ YOU'RE READY!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The migration is complete. Your bot can now trade:
  ✓ Spot markets (original mode)
  ✓ Futures markets (NEW!)
  ✓ Futures DEMO trading (sandbox: true + futures: true)

Start using it:

  $ python main.py


Questions? Check MIGRATION_FUTURES.md for details.
Happy trading! 🚀
""")