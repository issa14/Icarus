# Migration Icarus: Spot → Futures 🚀

**Date**: 2026-07-04  
**Status**: ✅ COMPLETED & TESTED

---

## Résumé Exécutif

La migration de **Icarus** vers les **marchés Futures** est **COMPLÈTE**. Le bot supporte maintenant:

- ✅ **Mode Spot** (hérité) - via `SpotExecutionController` + `SpotRiskController`
- ✅ **Mode Futures** - via `FuturesExecutionController` + `FuturesRiskController`
- ✅ **Factory Pattern** - sélection automatique via `exchange.futures` dans config
- ✅ **Backward Compatibility** - anciens projets restent fonctionnels

---

## Architecture Implémentée

### 1. Configuration (`icarus/config/models.py`)

**Nouveaux champs futures** dans `ScalpingConfig`:
```yaml
leverage: 1-125 (défaut: 1)
margin_mode: "cross" | "isolated" (défaut: "isolated")
hedge_mode: bool (défaut: false)
```

**Toggle** dans `ExchangeConfig`:
```yaml
futures: bool (défaut: false)
```

### 2. Types Partagés (`icarus/core/types.py`)

**Améliorations**:
- `OrderRequest.reduce_only: bool = False` → Pour clôtures sans solde d'ouverture
- `PositionSize.margin_usd: float = 0.0` → Marge engagée (0 pour Spot)

### 3. Contrôleurs d'Exécution

#### SpotExecutionController (renommé)
- **Fichier**: `icarus/execution/engine.py`
- **Classe**: `SpotExecutionController` (anciennement `ExecutionController`)
- **Responsabilités**: Ordres market/limit, monitoring, trailing stops

#### FuturesExecutionController (NOUVEAU)
- **Fichier**: `icarus/execution/futures.py`
- **Classe**: `FuturesExecutionController` (hérite de `SpotExecutionController`)
- **Améliorations**:
  - `_init_exchange()`: Active `defaultType: "future"` sur CCXT
  - `initialize_futures_account()`: Configure leverage + margin_mode par paire
  - `place_order()`: Ajoute flags CCXT (`reduceOnly`, `positionSide`)
  - Gère positions ouvertes avec `reduce_only=True` pour clôture

### 4. Contrôleurs de Risque

#### SpotRiskController (renommé)
- **Fichier**: `icarus/risk/engine.py`
- **Classe**: `SpotRiskController` (anciennement `RiskController`)
- **Responsabilités**: Validation signal, sizing Kelly, circuit breaker

#### FuturesRiskController (NOUVEAU)
- **Fichier**: `icarus/risk/futures.py`
- **Classe**: `FuturesRiskController` (implémente `RiskEngineInterface`)
- **Innovations**:
  - **`_calculate_futures_position()`**: 
    - Calcul marge: `margin = amount × entry / leverage`
    - Liquidation: `entry ± (entry/leverage) × 0.98`
    - Sécurité: Rejet si `|entry-sl| × 1.5 >= |entry-liquidation|`
  - **Sizing**: Kelly fractionnaire tenant compte de la marge disponible
  - **Persistence**: Tables SQLite partagées avec Spot (daily_pnl, trades_history)

### 5. Orchestrateur (Factory Pattern)

**Fichier**: `icarus/orchestrator.py`

**Factory Logic** dans `_init_components()`:
```python
if self._exchange_cfg.futures:
    self._risk = FuturesRiskController(...)
    self._execution = FuturesExecutionController(...)
else:
    self._risk = SpotRiskController(...)
    self._execution = SpotExecutionController(...)
```

**Initialisation Futures** dans `_prepare_execution()`:
```python
if self._exchange_cfg.futures and isinstance(self._execution, FuturesExecutionController):
    await self._execution.initialize_futures_account()
```

### 6. Exports Packages

#### `icarus/execution/__init__.py`
```python
from .engine import SpotExecutionController
from .futures import FuturesExecutionController
ExecutionController = SpotExecutionController  # Backward compat
```

#### `icarus/risk/__init__.py`
```python
from .engine import SpotRiskController
from .futures import FuturesRiskController
RiskController = SpotRiskController  # Backward compat
```

---

## Configuration YAML

### Activation Futures

**config.yaml** (Spot):
```yaml
exchange:
  futures: false  # ← Remain on Spot
```

**config.yaml** (Futures):
```yaml
scalping:
  leverage: 5
  margin_mode: cross  # ou isolated
  hedge_mode: false

exchange:
  futures: true  # ← Switch to Futures
```

---

## Tests Validés ✅

### Unit Tests (`test_futures_factory.py`)
```
✓ PASS: Imports (4 contrôleurs)
✓ PASS: Config Validation (leverage 1-125)
✓ PASS: Spot Factory
✓ PASS: Futures Factory
```

### Integration Tests (`test_orchestrator_integration.py`)
```
✓ PASS: Orchestrator Spot
✓ PASS: Orchestrator Futures
✓ PASS: Config File Loading
```

---

## Utilisation

### Démarrer en Spot (défaut)
```bash
python main.py --config config.yaml
# Ou sans argument (config.yaml par défaut)
python main.py
```

### Démarrer en Futures
```bash
# 1. Copier config.yaml.example → config.yaml
cp config.yaml.example config.yaml

# 2. Éditer config.yaml
vim config.yaml
# Mettre exchange.futures = true
# Configurer leverage, margin_mode

# 3. Lancer
python main.py
```

### Génération Config Exemple
```bash
python main.py --generate-config config.new.yaml
```

---

## Fichiers Modifiés / Créés

| Fichier | Status | Type |
|---------|--------|------|
| `icarus/config/models.py` | ✏️ Modifié | Config |
| `icarus/core/types.py` | ✏️ Modifié | Types |
| `icarus/execution/engine.py` | ✏️ Renommé | SpotExecution |
| `icarus/execution/futures.py` | ✨ NOUVEAU | FuturesExecution |
| `icarus/execution/__init__.py` | ✏️ Modifié | Package |
| `icarus/risk/engine.py` | ✏️ Renommé | SpotRisk |
| `icarus/risk/futures.py` | ✨ NOUVEAU | FuturesRisk |
| `icarus/risk/__init__.py` | ✏️ Modifié | Package |
| `icarus/orchestrator.py` | ✏️ Modifié | Factory |
| `config.yaml.example` | ✏️ Modifié | Config |
| `test_futures_factory.py` | ✨ NOUVEAU | Test |
| `test_orchestrator_integration.py` | ✨ NOUVEAU | Test |

---

## Points Clés de Sécurité

### Liquidation Futures
- Vérification: SL distance × 1.5 doit être < distance à liquidation
- Rejet automatique si risque de liquidation insuffisant
- Marge calculée avec buffer de 2% (98% du capital engagé)

### Ordres Futures
- `reduce_only=True` pour toutes les clôtures (anti-accumulation accidentelle)
- Position side ajustée si `hedge_mode=true`
- Leverage configuré **avant** le trade

### Risque
- Circuit breaker (max daily loss %) reste actif
- Kelly sizing adapté à la marge disponible
- Persistence SQLite unifiée (historique trades identique)

---

## Prochaines Étapes (Optionnel)

1. **Backtesting**: Valider rentabilité mode Futures vs Spot
2. **Sandbox**: Tester avec Binance Futures Demo Trading (sandbox: true + futures: true)
   → Le testnet public est déprécié ; utilisez les clés API générées sur https://demo.binance.com
3. **Monitoring**: Dashboard affiche mode actuel (Spot/Futures)
4. **Documentation**: Ajouter exemples stratégie futures

---

## Support

- **Config invalide**: Vérifier `exchange.futures: true|false`
- **Leverage rejeté**: Vérifier 1-125 et `margin_mode` ∈ {cross, isolated}
- **DB erreur**: Effacer `*.db` et redémarrer

---

**Migration complétée avec succès! 🚀**
