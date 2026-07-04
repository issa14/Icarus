"""icarus.config.models — Modèles Pydantic pour la validation de configuration.

Tous les paramètres sont validés avec des contraintes strictes.
Aucune valeur par défaut cachée — tout est explicite et documenté.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional


# ═══════════════════════════════════════════════════════════════════════════
# SOUS-MODÈLES
# ═══════════════════════════════════════════════════════════════════════════

class ExchangeConfig(BaseModel):
    """Configuration de connexion à l'exchange (CCXT)."""
    exchange:   str   = Field(default="binance", description="Nom de l'exchange CCXT")
    api_key:    str   = Field(default="", description="Clé API (vide = mode public)")
    api_secret: str   = Field(default="", description="Secret API (vide = mode public)")
    sandbox:    bool  = Field(default=False, description="Utiliser le testnet si disponible")


class DatabaseConfig(BaseModel):
    """Configuration de la base de données persistante."""
    host: str     = Field(default="localhost")
    port: int     = Field(default=5432, ge=1, le=65535)
    name: str     = Field(default="icarus")
    user: str     = Field(default="icarus")
    password: str = Field(default="")


class ScalpingConfig(BaseModel):
    """Configuration de la stratégie de scalping.

    Toutes les valeurs en pourcentage sont en décimal (ex: 0.005 = 0,5%).
    """

    # ── Marché ──
    symbols: List[str] = Field(
        default=["SOL/USDT", "HYPE/USDT", "DOGE/USDT"],
        min_length=1,
        max_length=5,
        description="Paires tradées (format CCXT: BASE/QUOTE). Le bot choisit la meilleure.",
    )

    @property
    def symbol(self) -> str:
        """Rétrocompatibilité : retourne la première paire."""
        return self.symbols[0]

    # ── Boucle d'exécution ──
    execution_loop_seconds: int = Field(
        default=5,
        ge=1, le=60,
        description="Intervalle entre deux itérations de la boucle principale (secondes)",
    )

    # ── Paramètres des ordres ──
    tp1_percent: float = Field(
        default=0.004, ge=0.001, le=0.05,
        description="Take-profit 1 en % du prix d'entrée (ex: 0.004 = 0,4%)",
    )
    tp2_percent: float = Field(
        default=0.007, ge=0.002, le=0.10,
        description="Take-profit 2 en % du prix d'entrée",
    )
    fixed_sl_percent: float = Field(
        default=0.005, ge=0.001, le=0.03,
        description="Stop-loss fixe en % du prix d'entrée",
    )
    tp1_fraction: float = Field(
        default=0.6, ge=0.3, le=0.8,
        description="Fraction de la position clôturée au TP1 (le reste au TP2)",
    )
    trailing_callback: float = Field(
        default=0.003, ge=0.0005, le=0.02,
        description="Callback du trailing stop (% depuis le plus haut/bas)",
    )
    trailing_activation: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Fraction de la distance au TP1 avant d'activer le trailing (0 = immédiat, 1 = désactivé)",
    )
    entry_timeout_seconds: int = Field(
        default=15, ge=5, le=60,
        description="Timeout d'un ordre limit d'entrée (secondes)",
    )

    # ── Seuils des indicateurs ──
    rsi_oversold: int = Field(
        default=35, ge=10, le=45,
        description="Seuil RSI de survente (signal long)",
    )
    rsi_overbought: int = Field(
        default=65, ge=55, le=90,
        description="Seuil RSI de surachat (signal short)",
    )
    volume_surge_threshold: float = Field(
        default=1.8, ge=1.2, le=5.0,
        description="Volume relatif minimum pour qualifier un surge (× moyenne)",
    )

    # ── Seuils de scoring ──
    threshold_base: float = Field(
        default=60.0, ge=40.0, le=80.0,
        description="Score minimum (0-100) pour déclencher un signal",
    )

    # ── Filtres de sécurité ──
    min_atr_percent: float = Field(
        default=0.0015, ge=0.0001, le=0.05,
        description="ATR minimum (en % du prix) pour accepter un trade",
    )
    max_atr_percent: float = Field(
        default=0.010, ge=0.001, le=0.20,
        description="ATR maximum (en % du prix) pour accepter un trade",
    )
    spread_max_percent: float = Field(
        default=0.0005, ge=0.0001, le=0.01,
        description="Spread maximum accepté (bid-ask / mid)",
    )

    # ── Gestion du risque ──
    risk_per_trade: float = Field(
        default=0.0025, ge=0.0005, le=0.02,
        description="Fraction du capital risquée par trade (Kelly implicite)",
    )
    max_daily_loss_percent: float = Field(
        default=0.02, ge=0.005, le=0.10,
        description="Perte quotidienne max avant circuit breaker",
    )
    max_positions: int = Field(
        default=2, ge=1, le=5,
        description="Nombre maximum de positions simultanées",
    )
    kelly_fraction: float = Field(
        default=0.25, ge=0.01, le=1.0,
        description="Fraction du Kelly full à utiliser (0.25 = 25%)",
    )

    # ── Cooldown ──
    cooldown_seconds: int = Field(
        default=120, ge=0, le=600,
        description="Délai minimum entre deux entrées (secondes)",
    )

    # ── Frais (simulation backtest) ──
    fee_maker: float = Field(
        default=0.0002, ge=0.0, le=0.01,
        description="Frais maker (entrée limit) — décimal",
    )
    fee_taker: float = Field(
        default=0.0004, ge=0.0, le=0.01,
        description="Frais taker (sortie market) — décimal",
    )

    # ── Validateurs ──

    @field_validator("tp2_percent")
    @classmethod
    def tp2_gt_tp1(cls, v: float, info) -> float:
        """TP2 doit être supérieur à TP1 (vérification après coup)."""
        return v

    @model_validator(mode="after")
    def check_consistency(self) -> "ScalpingConfig":
        """Vérifie la cohérence globale des paramètres."""
        if self.tp2_percent <= self.tp1_percent:
            raise ValueError(f"tp2_percent ({self.tp2_percent}) doit être > tp1_percent ({self.tp1_percent})")
        if self.fixed_sl_percent >= self.tp1_percent:
            raise ValueError(f"fixed_sl_percent ({self.fixed_sl_percent}) doit être < tp1_percent ({self.tp1_percent})")
        if self.min_atr_percent >= self.max_atr_percent:
            raise ValueError(f"min_atr_percent ({self.min_atr_percent}) doit être < max_atr_percent ({self.max_atr_percent})")
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError(f"rsi_oversold ({self.rsi_oversold}) doit être < rsi_overbought ({self.rsi_overbought})")
        return self


class TelegramConfig(BaseModel):
    """Configuration des notifications Telegram."""
    enabled: bool = Field(
        default=False,
        description="Activer les notifications Telegram. Nécessite un bot_token et chat_id.",
    )
    bot_token: str = Field(
        default="TON_TOKEN_BOTFATHER",
        description="Token du bot Telegram (créer via @BotFather)",
    )
    chat_id: str = Field(
        default="TON_CHAT_ID",
        description="Chat ID Telegram (obtenir via @userinfobot ou /start sur ton bot)",
    )
    notify_on_trade: bool = Field(
        default=True,
        description="Notifier à chaque trade clôturé",
    )
    notify_on_health: bool = Field(
        default=True,
        description="Notifier si un module devient UNHEALTHY",
    )
    daily_report: bool = Field(
        default=True,
        description="Envoyer un rapport quotidien récapitulatif",
    )
    daily_report_hour: int = Field(
        default=20, ge=0, le=23,
        description="Heure d'envoi du rapport quotidien (UTC)",
    )


class AppConfig(BaseModel):
    """Configuration applicative générale."""
    debug: bool          = Field(default=False)
    log_level: str       = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_file: str        = Field(default="icarus.log")
    dashboard: bool      = Field(default=True, description="Activer le dashboard console")


class HealthConfig(BaseModel):
    """Configuration des health checks."""
    interval_seconds: int       = Field(default=30, ge=10, le=300)
    stale_data_warning: int     = Field(default=15, ge=5, le=60, description="Délai avant alerte données stale (s)")
    stale_data_critical: int    = Field(default=30, ge=10, le=120)
    max_reconnect_attempts: int = Field(default=10, ge=1, le=100)


# ═══════════════════════════════════════════════════════════════════════════
# MODÈLE RACINE
# ═══════════════════════════════════════════════════════════════════════════

class BaseConfig(BaseModel):
    """Configuration racine du bot Icarus.

    Chargée depuis un fichier YAML et validée avec Pydantic.
    """
    scalping:  ScalpingConfig  = Field(default_factory=ScalpingConfig)
    exchange:  ExchangeConfig  = Field(default_factory=ExchangeConfig)
    telegram:  TelegramConfig  = Field(default_factory=TelegramConfig)
    database:  DatabaseConfig  = Field(default_factory=DatabaseConfig)
    app:       AppConfig       = Field(default_factory=AppConfig)
    health:    HealthConfig    = Field(default_factory=HealthConfig)
