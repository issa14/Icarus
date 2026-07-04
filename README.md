# 🤖 Icarus v2 — Bot de Scalping Intraday Multi-paires

**Capital cible : 100$** | **Exchange : Binance Spot** | **Timeframe : 1m**

Bot de trading algorithmique 100% automatisé spécialisé dans le **scalping intraday**. Architecture modulaire, validation Pydantic, multi-paires intelligent, notifications Telegram, gestion Kelly du risque.

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
    └────────────┘ └──────┘ └─────────────┘
             │
    ┌────────▼──────────┐
    │  Monitoring        │
    │  • Health Checks   │
    │  • Dashboard ANSI  │
    │  • Telegram Alerts │
    └────────────────────┘
```

### Modules

| Module | Fichier(s) | Rôle |
|--------|-----------|------|
| **core** | `types.py`, `interfaces.py`, `events.py` | Types, interfaces abstraites, EventBus |
| **config** | `models.py`, `loader.py` | Validation Pydantic, chargement YAML |
| **data** | `buffer.py`, `stream.py` | Ring buffer OHLCV, WebSocket Binance (Kline + Depth) |
| **signal** | `indicators.py`, `scoring.py`, `engine.py` | RSI, ATR, volume surge, score multi-critères |
| **risk** | `engine.py` | Kelly fractionnel, circuit breaker, sizing |
| **execution** | `engine.py` | Ordres limit/market, SL/TP, trailing stop |
| **monitoring** | `health.py`, `dashboard.py`, `telegram.py` | Health checks, dashboard ANSI temps réel, notifications Telegram |
| **orchestrator.py** | → | Cœur du bot, coordination multi-paires |

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

---

## ✅ Ce qui est fait

- [x] Architecture modulaire avec interfaces abstraites (ABC)
- [x] Configuration YAML validée par Pydantic (fail-fast)
- [x] Multi-paires intelligent (N DataProviders → meilleur signal)
- [x] WebSocket Binance : Kline (1m) + Depth (order book)
- [x] Ring buffer OHLCV (taille configurable)
- [x] Indicateurs : RSI, ATR, volume surge, spread
- [x] Scoring multi-critères pondéré
- [x] Gestion des risques : Kelly fractionnel, circuit breaker, cooldown
- [x] Execution engine : ordres limit, SL/TP, trailing stop
- [x] Health monitoring : heartbeat, stale data detection
- [x] Dashboard console ANSI temps réel
- [x] Notifications Telegram (trades, santé, rapport quotidien)
- [x] Graceful shutdown (Ctrl+C → annulation ordres + fermeture connexions)
- [x] EventBus (pub/sub) prêt pour extension
- [x] Injection de dépendances (modules interchangeables)
- [x] `.gitignore` : config.yaml, bases SQLite, logs exclus

## 🔜 Ce qui reste à faire

### Priorité haute
- [ ] **Tests unitaires** sur chaque module (pytest)
- [ ] **Backtest engine** sur données historiques (validate la stratégie)
- [ ] **Parameter optimizer** (grid search / bayesien) sur la nouvelle archi
- [ ] **Mode paper trading** (sandbox Binance avec suivi PnL)
- [ ] **Dashboard web** (Flask/FastAPI + Chart.js) au lieu de la console

### Priorité moyenne
- [ ] **Gestion short** (actuellement LONG uniquement — Binance Spot)
- [ ] **Multi-exchange** (Bybit, Kraken via CCXT)
- [ ] **Persistance avancée** (PostgreSQL pour trades + métriques)
- [ ] **Alertes configurables** (Telegram, Discord, email)
- [ ] **Détection de ranging market** (filtre à volatilité insuffisante)

### Priorité basse
- [ ] **Machine learning** (prédiction de probabilité de win via features OHLCV)
- [ ] **Backtesting multi-symbols** simultané
- [ ] **CLI interactive** (mode manuel, simulation)
- [ ] **Docker** + docker-compose
- [ ] **CI/CD** (GitHub Actions → tests + lint)

---

## 🏗️ Structure du projet

```
Icarus/
├── main.py                    # Point d'entrée
├── config.yaml                # Configuration (⚠️ .gitignored)
├── config.yaml.example        # Modèle de configuration (distribué)
├── requirements.txt           # Dépendances Python
├── README.md                  # ← Ce fichier
├── .gitignore
│
└── icarus/
    ├── core/
    │   ├── __init__.py
    │   ├── types.py           # Dataclasses : MarketSnapshot, TradingSignal, etc.
    │   ├── interfaces.py      # ABC : DataProvider, SignalEngine, etc.
    │   └── events.py          # EventBus (pub/sub)
    │
    ├── config/
    │   ├── __init__.py
    │   ├── models.py          # Modèles Pydantic (validation)
    │   └── loader.py          # Chargement YAML
    │
    ├── data/
    │   ├── __init__.py
    │   ├── buffer.py          # Ring buffer OHLCV
    │   └── stream.py          # BinanceDataProvider (WebSocket)
    │
    ├── signal/
    │   ├── __init__.py
    │   ├── indicators.py      # RSI, ATR, volume surge
    │   ├── scoring.py          # Score composite
    │   └── engine.py           # ScalpingSignalEngine
    │
    ├── risk/
    │   ├── __init__.py
    │   └── engine.py           # RiskController (Kelly, circuit breaker)
    │
    ├── execution/
    │   ├── __init__.py
    │   └── engine.py           # ExecutionController (ordres, SL/TP)
    │
    ├── monitoring/
    │   ├── __init__.py
    │   ├── health.py           # HealthMonitor
    │   ├── dashboard.py        # Dashboard ANSI
    │   └── telegram.py         # TelegramNotifier (async)
    │
    └── orchestrator.py         # Cœur du bot (coordination multi-paires)
```

---

## ⚠️ Disclaimer

**Ce bot est fourni à titre éducatif. Le trading de crypto-monnaies comporte des risques de perte en capital. Ne tradez jamais plus que ce que vous êtes prêt à perdre. Les performances passées ne garantissent pas les résultats futurs.**

Commencez toujours en **sandbox** (`sandbox: true`) avant de passer en réel.

---

## 📄 Licence

MIT — voir le fichier LICENSE (à créer).