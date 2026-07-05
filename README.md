# 🤖 Icarus v3.1 — Bot de Scalping Intraday Futures (USDS-M)

**Capital cible : 100$** | **Exchange : Binance Futures (USDS-M)** | **Timeframe : 1m**

Bot de trading algorithmique 100% automatisé spécialisé dans le **scalping intraday sur Futures**. Architecture modulaire, validation Pydantic, multi-paires intelligent, notifications Telegram, gestion Kelly du risque. **100% Futures (USDS-M)** — le mode Spot a été définitivement retiré en v3.1. Backtesting intégré, grid search optimizer, et détection de marché rangeant.

---

## 🧠 Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      main.py                            │
│                  (entry point)                          │
└──────────────────────┬──────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │    Orchestrateur         │
          │  (icarus/orchestrator)  │
          │                         │
          │  • Futures-only         │
          │    USDS-M uniquement    │
          │  • Choisit la meilleure │
          │    paire parmi N        │
          │  • Injections DI        │
          │  • Graceful shutdown    │
          └──┬───────┬───────┬──────┘
             │       │       │
    ┌────────▼──┐ ┌──▼───┐ ┌─▼──────────┐
    │  Data      │ │Signal│ │ Execution   │
    │  Providers │ │Engine│ │ Controller  │
    │  (N paires)│ │      │ │             │
    │            │ │      │ │ • Futures   │
    └────────────┘ └──┬───┘ └─────────────┘
             │        │
    ┌────────▼────────▼──┐
    │  Monitoring         │
    │  • Health Checks    │
    │  • Dashboard ANSI   │
    │  • Telegram Alerts  │
    └─────────────────────┘

    ┌─────────────────────┐
    │  Backtesting         │
    │  • Backtest Engine   │
    │  • Futures Engine    │
    │  • Grid Search       │
    │  • Métriques         │
    └─────────────────────┘
```

### Modules

| Module | Fichier(s) | Rôle |
|--------|-----------|------|
| **core** | `types.py`, `interfaces.py`, `events.py` | Types, interfaces abstraites, EventBus |
| **config** | `models.py`, `loader.py` | Validation Pydantic, chargement YAML |
| **data** | `buffer.py`, `stream.py` | Ring buffer OHLCV, WebSocket Binance (Kline + Depth) |
| **signal** | `indicators.py`, `scoring.py`, `engine.py`, `market_state.py` | RSI, ATR, volume surge, score multi-critères, **détection marché rangeant** |
| **risk** | `engine.py`, `futures.py` | Kelly fractionnel, circuit breaker, sizing, liquidation check |
| **execution** | `engine.py`, `futures.py` | Ordres limit/market, SL/TP, trailing stop, leverage, reduce-only |
| **backtest** | `engine.py`, `futures_engine.py`, `metrics.py`, `optimizer.py` | Backtesting Futures & Legacy, grid search, métriques avancées |
| **monitoring** | `health.py`, `dashboard.py`, `telegram.py` | Health checks, dashboard ANSI temps réel, notifications Telegram |
| **orchestrator.py** | → | Cœur du bot, coordination multi-paires, futures-only |

---

## 🚀 Installation

```bash
# 1. Cloner le repo
git clone https://github.com/issa14/Icarus.git
cd Icarus

# 2. Créer un virtualenv (optionnel mais recommandé)
python -m venv venv
source venv/bin/activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Copier la config exemple
cp config.yaml.example config.yaml

# 5. Éditer config.yaml avec vos clés
nano config.yaml
```

### Dépendances

```
ccxt>=4.0.0          # Abstraction multi-exchanges
websockets>=12.0     # WebSocket Binance
pyyaml>=6.0          # Parsing YAML
pydantic>=2.0.0      # Validation de config
colorama>=0.4.6      # Couleurs dashboard
pandas>=2.0.0        # Manipulation de données
numpy>=1.24.0        # Calculs numériques
aiohttp>=3.9.0       # Notifications Telegram (optionnel)
```

---

## ▶️ Utilisation

```bash
# Mode sandbox (testnet Binance — pas de vrais trades)
python main.py

# Le bot va :
# 1. Valider la configuration (Pydantic)
# 2. Démarrer les WebSockets (Kline + Depth pour chaque paire)
# 3. Attendre 50+ bougies 1m (pré-chauffage des indicateurs)
# 4. Lancer la boucle de trading (scan toutes les 5s)
```

### Mode Futures (le seul disponible)

```bash
# 1. Configurer config.yaml — le mode Futures est le seul supporté.
#    scalping.leverage = 1-125
#    scalping.margin_mode = "cross" | "isolated"

# 2. Lancer
python main.py

# Mode Demo Trading (sandbox Futures)
#    exchange.sandbox = true
#    → Demo trading activé automatiquement (demo-fapi.binance.com)
```

> ⚠️ **Note v3.1** : Le mode Spot a été définitivement retiré. Icarus est désormais **100% Futures (USDS-M)**.  
> Le flag `exchange.futures` n'existe plus — le bot est implicitement Futures.  
> Voir [QUICK_START_FUTURES.md](./QUICK_START_FUTURES.md) pour le guide de démarrage.

---

## ⚙️ Configuration détaillée (`config.yaml`)

### Paires tradées

```yaml
symbols:
  - SOL/USDT       # 100-150$ / unité
  - HYPE/USDT      # 20-30$ / unité
  - DOGE/USDT      # <1$ / unité
```

Le bot scanne **toutes les paires simultanément** et ne trade que la meilleure opportunité. 1 seul trade à la fois.

### Configuration Futures (100% Futures)

```yaml
scalping:
  # Paramètres Futures — toujours actifs
  leverage: 5           # 1 à 125
  margin_mode: isolated # "cross" ou "isolated"
  hedge_mode: false     # Mode hedge Binance
```

> ℹ️ Le flag `exchange.futures` a été supprimé en v3.1. Le mode est implicitement Futures.

### Paramètres des ordres (optimisés scalping 1m)

| Paramètre | Valeur | Signification |
|-----------|--------|---------------|
| `tp1_percent` | 0.006 (0.6%) | Take-profit 1 |
| `tp2_percent` | 0.010 (1.0%) | Take-profit 2 (runner) |
| `fixed_sl_percent` | 0.004 (0.4%) | Stop-loss |
| `tp1_fraction` | 0.6 (60%) | Part clôturée au TP1 (le reste runner) |
| `trailing_callback` | 0.3% | Callback du trailing stop |
| `trailing_activation` | 50% | Activation à mi-chemin du TP1 |

### Gestion du risque (optimisé 100$)

| Paramètre | Valeur | Impact |
|-----------|--------|--------|
| `risk_per_trade` | 0.25% | **0.25$ risqué par trade** |
| `max_daily_loss` | 2% | Circuit breaker à **-2$** |
| `kelly_fraction` | 15% | Ultra-conservateur |
| `cooldown_seconds` | 180 | 3 min minimum entre 2 trades |
| `max_positions` | 1 | Capital concentré |
| `threshold_base` | 65 | Score minimum élevé → moins de faux signaux |

---

## 📊 Multi-paires intelligent

À chaque itération (5 secondes) :

```
SOL/USDT  ──► snap ──► signal ──► score = 72  ← 🥇 MEILLEUR
HYPE/USDT ──► snap ──► signal ──► score = 45
DOGE/USDT ──► snap ──► signal ──► score = 13

→ Score SOL (72) ≥ threshold (65) ? OUI → TRADE SOL/USDT
→ Cooldown 180s
→ Prochaine itération : re-scanne tout
```

Avantages :
- ✅ 3× plus d'opportunités qu'un bot single-pair
- ✅ Capital concentré sur le setup le plus prometteur
- ✅ Diversification implicite (les paires ne sont pas corrélées)

---

## 🧪 Backtesting

Le framework de backtesting supporte le mode **Futures** (moteur principal) et un mode **legacy** (sans liquidation).

### Backtest Futures

```bash
python run_backtest_futures.py
```

Utilise le `FuturesBacktestEngine` avec support du leverage, marge, liquidation et PnL Futures.

### Optimisation de paramètres (Grid Search)

```bash
# Grid search Futures (recommandé)
python -m icarus.backtest.optimizer --futures
```

Grid search sur les paramètres clés (RSI, seuils, TP/SL) avec métrique composite.

### Modules Backtest

| Fichier | Rôle |
|---------|------|
| `icarus/backtest/engine.py` | Moteur legacy (simulation bougie par bougie) |
| `icarus/backtest/futures_engine.py` | Moteur Futures (leverage, marge, liquidation) |
| `icarus/backtest/metrics.py` | Calcul métriques (Sharpe, drawdown, win rate, etc.) |
| `icarus/backtest/optimizer.py` | Grid search Futures & Legacy, scoring composite |

---

## 🎯 Détection de Marché Rangeant

Le module `icarus/signal/market_state.py` implémente un **filtre anti-range** pour éviter de scalper dans un marché sans direction.

| Fonction | Rôle |
|----------|------|
| `compute_range_amplitude()` | Amplitude du range sur N bougies (% du prix médian) |
| `compute_ranging_score()` | Score continu [0,1] — 0 = trending, 1 = ranging |
| `should_skip_ranging()` | Décision binaire : skipper le trade si score ≥ seuil |
| `expansion_detected()` | Détection d'expansion imminente (sortie de range avec volume) |

Le score combine trois facteurs pondérés :
1. **Amplitude relative** (60%) — faible amplitude → ranging
2. **Caractère oscillant** (25%) — alternance hausse/baisse
3. **Cohérence directionnelle** (15%) — pente des closes

---

## 📱 Telegram v2 — Notifications & Commandes interactives

Le bot Telegram a été entièrement refondu (v2) avec :

- **Queue asyncio non-bloquante** — les notifications n'impactent pas la boucle de trading
- **Retry avec exponential backoff** (3 tentatives, configurable)
- **Rate limiter** (token bucket, 20 msg/sec par défaut)
- **Commandes interactives** — tu peux piloter le bot depuis Telegram

### Configuration

```yaml
telegram:
  enabled: true
  bot_token: "TON_TOKEN"
  chat_id: "TON_CHAT_ID"
  notify_on_trade: true
  notify_on_position_open: true    # 🆕 Alerte à l'ouverture de position
  notify_on_health: true
  daily_report: true
  daily_report_hour: 20
  # ── Robustesse ──
  retry_max_attempts: 3
  retry_base_delay: 1.0
  retry_max_delay: 30.0
  rate_limit_max: 20.0
  # ── Commandes interactives ──
  command_enabled: true
  command_polling_interval: 2
  admin_chat_ids: []
```

### Notifications automatiques (push)

| Événement | Message |
|-----------|---------|
| 🚀 Démarrage | Config résumée (paires, TP/SL, risque) |
| 🔔 Nouvelle position | Entrée, SL, TP1/TP2, taille, risque, score, levier |
| 🟢/🔴 Trade clôturé | PnL, prix entrée/sortie, ⏱️ durée, raison |
| 🚨 Alerte santé | Module degraded/unhealthy |
| 📊 Rapport quotidien | PnL jour, win rate, solde |
| 🛑 Arrêt | Notification d'arrêt propre |

### Commandes interactives (pull)

| Commande | Description |
|----------|-------------|
| `/start` | Message de bienvenue + liste des commandes |
| `/help` | Rappel des commandes disponibles |
| `/status` | État global (PnL, positions, uptime, solde, cooldown) |
| `/pnl` | Détail des performances (W/L, win rate, drawdown) |
| `/positions` | Positions ouvertes (entrée, qté, PnL latent) |
| `/config` | Configuration active (paires, TP%, SL%, levier…) |
| `/pause` | Met en pause les nouveaux signaux (positions gérées) |
| `/resume` | Reprend le scan de signaux |
| `/stop` | Arrêt graceful du bot |

---

## 🛡️ Gestion des risques

### Circuit breaker
- Perte quotidienne ≥ 2% du capital → **arrêt immédiat** pour la journée
- Exemple 100$ : si perte cumulée ≥ 2$, le bot s'arrête

### Kelly fractionnel
- Formule : `size = Kelly_full * kelly_fraction`
- Avec `kelly_fraction = 0.15` (15%), on utilise seulement 15% du sizing théorique
- Cela protège contre l'incertitude des paramètres estimés

### Cooldown
- 3 minutes entre chaque trade pour éviter l'overtrading
- Laisse le marché "respirer" après une entrée

### Protection Futures
- Vérification distance SL vs liquidation (rejet si trop proche)
- `reduce_only=True` sur toutes les clôtures pour éviter l'accumulation accidentelle
- Marge calculée avec buffer de sécurité (98% du capital engagé)

---

## ✅ Ce qui est fait

- [x] Architecture modulaire avec interfaces abstraites (ABC)
- [x] Configuration YAML validée par Pydantic (fail-fast)
- [x] Multi-paires intelligent (N DataProviders → meilleur signal)
- [x] WebSocket Binance : Kline (1m) + Depth (order book)
- [x] Ring buffer OHLCV (taille configurable)
- [x] Indicateurs : RSI, ATR, volume surge, spread
- [x] Scoring multi-critères pondéré
- [x] **Détection de marché rangeant** (anti-range filter)
- [x] **Support Futures** : FuturesExecutionController (100% USDS-M)
- [x] **Risk Futures** : Kelly fractionnel avec marge, liquidation check
- [x] **Futures-only** : mode Spot retiré en v3.1, code simplifié
- [x] **Demo Trading Futures** (demo-fapi.binance.com)
- [x] **Backtesting** : simulation bougie par bougie
- [x] **Backtesting Futures** : leverage, marge, liquidation
- [x] **Grid Search** : optimisation paramètres Futures & Legacy
- [x] **Métriques avancées** : Sharpe, drawdown, profit factor, etc.
- [x] Gestion des risques : Kelly fractionnel, circuit breaker, cooldown
- [x] Execution engine : ordres limit, SL/TP, trailing stop
- [x] Health monitoring : heartbeat, stale data detection
- [x] Dashboard console ANSI temps réel
- [x] Notifications Telegram (trades, santé, rapport quotidien)
- [x] Graceful shutdown (Ctrl+C → annulation ordres + fermeture connexions)
- [x] EventBus (pub/sub) prêt pour extension
- [x] Injection de dépendances (modules interchangeables)
- [x] `.gitignore` : config.yaml, bases SQLite, logs exclus
- [x] Tests unitaires : métriques backtest
- [x] Script de téléchargement de données historiques

## 🔜 Ce qui reste à faire

### Priorité haute
- [ ] **Mode paper trading** (sandbox Binance avec suivi PnL)
- [ ] **Dashboard web** (Flask/FastAPI + Chart.js) au lieu de la console
- [ ] **Backtesting multi-symbols** simultané
- [ ] **Walk-forward optimization**

### Priorité moyenne
- [ ] **Gestion short** (actuellement LONG uniquement)
- [ ] **Multi-exchange** (Bybit, Kraken via CCXT)
- [ ] **Persistance avancée** (PostgreSQL pour trades + métriques)
- [ ] **Alertes configurables** (Discord, email)
- [ ] **Docker** + docker-compose

### Priorité basse
- [ ] **Machine learning** (prédiction de probabilité de win via features OHLCV)
- [ ] **CLI interactive** (mode manuel, simulation)
- [ ] **CI/CD** (GitHub Actions → tests + lint)

---

## 🗂️ Structure du projet

```
Icarus/
├── main.py                       # Point d'entrée
├── config.yaml                   # Configuration (⚠️ .gitignored)
├── config.yaml.example           # Modèle de configuration (distribué)
├── requirements.txt              # Dépendances Python
├── README.md                     # ← Ce fichier
├── QUICK_START_FUTURES.md        # Guide démarrage Futures
├── MIGRATION_FUTURES.md          # Détails migration Spot → Futures (v3.0)
├── .gitignore
│
├── run_backtest.py               # Lancement backtest Legacy
├── run_backtest_futures.py       # Lancement backtest Futures
├── run_optimizer_oos.py          # Grid search paramètres
├── validate_migration.py         # Script de validation migration (v3.0)
│
├── tests/
│   └── test_backtest_metrics.py  # Tests unitaires métriques backtest
│
├── scripts/
│   └── download_data.py          # Téléchargement données historiques
│
└── icarus/
    ├── orchestrator.py           # Cœur du bot (coordination multi-paires, futures-only)

    ├── core/
    │   ├── __init__.py
    │   ├── types.py              # Dataclasses : MarketSnapshot, TradingSignal, etc.
    │   ├── interfaces.py         # ABC : DataProvider, SignalEngine, etc.
    │   └── events.py             # EventBus (pub/sub)

    ├── config/
    │   ├── __init__.py
    │   ├── models.py             # Modèles Pydantic (validation, 100% Futures)
    │   └── loader.py             # Chargement YAML

    ├── data/
    │   ├── __init__.py
    │   ├── buffer.py             # Ring buffer OHLCV
    │   └── stream.py             # BinanceDataProvider (WebSocket futures)

    ├── signal/
    │   ├── __init__.py
    │   ├── indicators.py         # RSI, ATR, volume surge
    │   ├── scoring.py            # Score composite
    │   ├── engine.py             # ScalpingSignalEngine
    │   └── market_state.py       # Détection marché rangeant + expansion

    ├── risk/
    │   ├── __init__.py
    │   ├── engine.py             # RiskController de base (Kelly, circuit breaker)
    │   └── futures.py            # FuturesRiskController (marge, liquidation)

    ├── execution/
    │   ├── __init__.py
    │   ├── engine.py             # ExecutionController de base (ordres, SL/TP)
    │   └── futures.py            # FuturesExecutionController (leverage, reduce-only)

    ├── backtest/
    │   ├── __init__.py
    │   ├── engine.py             # BacktestEngine & BacktestResult (legacy)
    │   ├── futures_engine.py     # FuturesBacktestEngine & FuturesBacktestResult
    │   ├── metrics.py            # compute_metrics (Sharpe, drawdown, etc.)
    │   └── optimizer.py          # Grid search Futures & Legacy

    ├── monitoring/
    │   ├── __init__.py
    │   ├── health.py             # HealthMonitor
    │   ├── dashboard.py          # Dashboard ANSI
    │   └── telegram.py           # TelegramNotifier (async)

    └── cache/
        └── .gitkeep              # Dossier cache pour données téléchargées
```

---

## ⚠️ Disclaimer

**Ce bot est fourni à titre éducatif. Le trading de crypto-monnaies comporte des risques de perte en capital. Ne tradez jamais plus que ce que vous êtes prêt à perdre. Les performances passées ne garantissent pas les résultats futurs. Le trading avec effet de levier (Futures) peut entraîner des pertes supérieures au capital investi.**

Commencez toujours en **sandbox/demo trading** (`sandbox: true`) avant de passer en réel.

---

## 📚 Documentation supplémentaire

- **[QUICK_START_FUTURES.md](./QUICK_START_FUTURES.md)** — Guide de démarrage rapide Futures, FAQ, checklist sécurité
- **[MIGRATION_FUTURES.md](./MIGRATION_FUTURES.md)** — Détails techniques de la migration Spot → Futures (v3.0)

---

## 📄 Licence

MIT — voir le fichier LICENSE (à créer).