# 🤖 Icarus v2 — Bot de Scalping Intraday Multi-paires

**Capital cible : 100$** | **Exchange : Binance Spot & Futures** | **Timeframe : 1m**

Bot de trading algorithmique 100% automatisé spécialisé dans le **scalping intraday**. Architecture modulaire, validation Pydantic, multi-paires intelligent, notifications Telegram, gestion Kelly du risque, support **Spot et Futures** avec factory pattern, backtesting intégré et détection de marché rangeant.

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
          │  • Factory Pattern      │
          │    Spot ↔ Futures       │
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
    │            │ │      │ │ • Spot      │
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
    │  • Spot Engine       │
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
| **risk** | `engine.py` (Spot), `futures.py` (Futures) | Kelly fractionnel, circuit breaker, sizing, liquidation check |
| **execution** | `engine.py` (Spot), `futures.py` (Futures) | Ordres limit/market, SL/TP, trailing stop, leverage, reduce-only |
| **backtest** | `engine.py`, `futures_engine.py`, `metrics.py`, `optimizer.py` | Backtesting Spot & Futures, grid search, métriques avancées |
| **monitoring** | `health.py`, `dashboard.py`, `telegram.py` | Health checks, dashboard ANSI temps réel, notifications Telegram |
| **orchestrator.py** | → | Cœur du bot, coordination multi-paires, factory Spot/Futures |

---

## 🚀 Installation

```bash
# 1. Cloner le repo
git clone <repo-url>
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

### Mode Futures

```bash
# 1. Configurer config.yaml avec futures: true
#    exchange.futures = true
#    scalping.leverage = 1-125
#    scalping.margin_mode = "cross" | "isolated"

# 2. Lancer en mode Futures
python main.py

# Mode Demo Trading (sandbox Futures)
#    exchange.sandbox = true
#    exchange.futures = true
#    → Demo trading activé automatiquement (demo-fapi.binance.com)
```

Voir [QUICK_START_FUTURES.md](./QUICK_START_FUTURES.md) pour le guide complet de migration Spot → Futures.

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

### Mode Spot vs Futures

```yaml
exchange:
  futures: false     # false = Spot (défaut), true = Futures

scalping:
  # Paramètres Futures (ignorés si exchange.futures = false)
  leverage: 5           # 1 à 125
  margin_mode: isolated # "cross" ou "isolated"
  hedge_mode: false     # Mode hedge Binance
```

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

Le framework de backtesting supporte les modes **Spot** et **Futures** avec moteurs dédiés.

### Backtest Spot

```bash
python run_backtest.py
```

Utilise le `BacktestEngine` pour simuler la stratégie sur données historiques OHLCV.

### Backtest Futures

```bash
python run_backtest_futures.py
```

Utilise le `FuturesBacktestEngine` avec support du leverage, marge, liquidation et PnL Futures.

### Optimisation de paramètres (Grid Search)

```bash
python run_optimizer_oos.py
```

Grid search sur les paramètres clés (RSI, seuils, TP/SL) avec métrique composite.

### Modules Backtest

| Fichier | Rôle |
|---------|------|
| `icarus/backtest/engine.py` | Moteur Spot (simulation bougie par bougie) |
| `icarus/backtest/futures_engine.py` | Moteur Futures (leverage, marge, liquidation) |
| `icarus/backtest/metrics.py` | Calcul métriques (Sharpe, drawdown, win rate, etc.) |
| `icarus/backtest/optimizer.py` | Grid search Spot & Futures, scoring composite |

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

## 📱 Telegram

Pour activer les notifications :

1. Créer un bot via [@BotFather](https://t.me/BotFather) sur Telegram
2. Récupérer le token (ex: `123456:ABC-DEF...`)
3. Envoyer `/start` à ton bot, puis demander ton chat_id à [@userinfobot](https://t.me/userinfobot)
4. Remplir `config.yaml` :

```yaml
telegram:
  enabled: true
  bot_token: "TON_TOKEN"
  chat_id: "TON_CHAT_ID"
```

### Notifications envoyées

| Événement | Message |
|-----------|---------|
| 🚀 Démarrage | Config résumée (paires, TP/SL, risque) |
| 🟢/🔴 Trade clôturé | PnL, prix entrée/sortie, raison |
| 🚨 Alerte santé | Module degraded/unhealthy |
| 📊 Rapport quotidien | PnL jour, win rate, solde |
| 🛑 Arrêt | Notification d'arrêt propre |

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
- [x] **Support Futures** : SpotExecutionController + FuturesExecutionController
- [x] **Risk Futures** : Kelly fractionnel avec marge, liquidation check
- [x] **Factory Pattern** : sélection automatique Spot/Futures via config
- [x] **Demo Trading Futures** (demo-fapi.binance.com)
- [x] **Backtesting Spot** : simulation bougie par bougie
- [x] **Backtesting Futures** : leverage, marge, liquidation
- [x] **Grid Search** : optimisation paramètres Spot & Futures
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
- [x] Tests unitaires : factory Spot/Futures, orchestrateur, métriques backtest
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
├── MIGRATION_FUTURES.md          # Détails migration Spot → Futures
├── .gitignore
│
├── run_backtest.py               # Lancement backtest Spot
├── run_backtest_futures.py       # Lancement backtest Futures
├── run_optimizer_oos.py          # Grid search paramètres
├── validate_migration.py         # Validation migration Spot/Futures
│
├── tests/
│   └── test_backtest_metrics.py  # Tests unitaires métriques backtest
│
├── scripts/
│   └── download_data.py          # Téléchargement données historiques
│
└── icarus/
    ├── orchestrator.py           # Cœur du bot (coordination multi-paires, factory)

    ├── core/
    │   ├── __init__.py
    │   ├── types.py              # Dataclasses : MarketSnapshot, TradingSignal, etc.
    │   ├── interfaces.py         # ABC : DataProvider, SignalEngine, etc.
    │   └── events.py             # EventBus (pub/sub)

    ├── config/
    │   ├── __init__.py
    │   ├── models.py             # Modèles Pydantic (validation, support Futures)
    │   └── loader.py             # Chargement YAML

    ├── data/
    │   ├── __init__.py
    │   ├── buffer.py             # Ring buffer OHLCV
    │   └── stream.py             # BinanceDataProvider (WebSocket)

    ├── signal/
    │   ├── __init__.py
    │   ├── indicators.py         # RSI, ATR, volume surge
    │   ├── scoring.py            # Score composite
    │   ├── engine.py             # ScalpingSignalEngine
    │   └── market_state.py       # Détection marché rangeant + expansion

    ├── risk/
    │   ├── __init__.py
    │   ├── engine.py             # SpotRiskController (Kelly, circuit breaker)
    │   └── futures.py            # FuturesRiskController (marge, liquidation)

    ├── execution/
    │   ├── __init__.py
    │   ├── engine.py             # SpotExecutionController (ordres, SL/TP)
    │   └── futures.py            # FuturesExecutionController (leverage, reduce-only)

    ├── backtest/
    │   ├── __init__.py
    │   ├── engine.py             # BacktestEngine & BacktestResult (Spot)
    │   ├── futures_engine.py     # FuturesBacktestEngine & FuturesBacktestResult
    │   ├── metrics.py            # compute_metrics (Sharpe, drawdown, etc.)
    │   └── optimizer.py          # Grid search Spot & Futures

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
- **[MIGRATION_FUTURES.md](./MIGRATION_FUTURES.md)** — Détails techniques de la migration Spot → Futures

---

## 📄 Licence

MIT — voir le fichier LICENSE (à créer).