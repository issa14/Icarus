"""icarus.core.types — Dataclasses partagées pour toute l'application.

Toutes les structures de données transitant entre les modules sont définies ici.
Cela garantit un contrat clair et évite les dicts non-typés.

═══════════════════════════════════════════════════════════════════════════════
CONVENTIONS
═══════════════════════════════════════════════════════════════════════════════
• Tout est dataclass (slots=True pour la performance, frozen=True quand immuable).
• Les timestamps sont en secondes Unix (float).
• Les quantités sont en unités de base (BTC, ETH, etc.), les prix en USDT.
• Les valeurs optionnelles sont explicitement à None plutôt qu'omises.

═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class Direction(StrEnum):
    """Direction d'un trade."""
    LONG  = "LONG"
    SHORT = "SHORT"


class TradeAction(StrEnum):
    """Action envoyée par le SignalEngine."""
    BUY  = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


class ExitReason(StrEnum):
    """Raison de clôture d'un trade."""
    TP1        = "TP1"
    TP2        = "TP2"
    SL         = "SL"
    TRAILING_SL = "TrailingSL"
    MANUAL     = "manual"


class OrderStatus(StrEnum):
    """Statut d'un ordre envoyé à l'exchange."""
    PENDING     = "pending"
    FILLED      = "filled"
    PARTIALLY   = "partially_filled"
    CANCELLED   = "cancelled"
    EXPIRED     = "expired"
    REJECTED    = "rejected"
    UNKNOWN     = "unknown"


class MarketRegime(StrEnum):
    """Régime de marché détecté par le SignalEngine."""
    TREND_UP    = "trend_up"
    TREND_DOWN  = "trend_down"
    RANGE       = "range"
    VOLATILE    = "volatile"
    UNKNOWN     = "unknown"


class ComponentStatus(StrEnum):
    """Statut d'un composant pour le health check."""
    HEALTHY   = "healthy"
    DEGRADED  = "degraded"
    UNHEALTHY = "unhealthy"


# ═══════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Candle:
    """Une bougie OHLCV (1 minute).
    
    Attributes
    ----------
    timestamp_ms: int
        Timestamp d'ouverture de la bougie en millisecondes (convention Binance).
    open, high, low, close: float
        Prix OHLC.
    volume: float
        Volume en unités de base (BTC, ETH, etc.).
    is_closed: bool
        True si la bougie est fermée (utile pour les bougies en cours).
    """
    timestamp_ms: int
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float
    is_closed: bool = True

    @property
    def timestamp(self) -> float:
        """Timestamp en secondes Unix (float)."""
        return self.timestamp_ms / 1000.0

    @classmethod
    def from_binance_kline(cls, kline_data: dict) -> "Candle":
        """Construit une Candle depuis un message WebSocket Binance kline_1m."""
        k = kline_data["k"]
        return cls(
            timestamp_ms=int(k["t"]),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            is_closed=bool(k["x"]),
        )


@dataclass(frozen=True, slots=True)
class MicroStructure:
    """Microstructure instantanée du carnet d'ordres (top 10 agrégé).
    
    Imbalance
    ---------
    •  0.5  = neutre (même volume bid/ask)
    • >0.6  = pression acheteuse (signal long favorable)
    • <0.4  = pression vendeuse (signal short favorable)
    """
    bid_price:   float
    ask_price:   float
    bid_volume:  float
    ask_volume:  float
    mid_price:   float
    spread:      float      # (ask-bid)/mid
    imbalance:   float      # bid_volume / (bid_volume + ask_volume)
    last_update: float      # timestamp Unix (secondes)

    @classmethod
    def from_binance_depth(cls, depth_data: dict) -> Optional["MicroStructure"]:
        """Construit une MicroStructure depuis un message WebSocket depth10."""
        bids = depth_data.get("b", [])
        asks = depth_data.get("a", [])
        if not bids or not asks:
            return None
        bid_price  = float(bids[0][0])
        bid_volume = float(bids[0][1])
        ask_price  = float(asks[0][0])
        ask_volume = float(asks[0][1])
        mid   = (bid_price + ask_price) / 2.0
        spread = (ask_price - bid_price) / mid if mid > 0 else 0.0
        total_vol = bid_volume + ask_volume
        imbalance = bid_volume / total_vol if total_vol > 0 else 0.5
        return cls(
            bid_price=bid_price,
            ask_price=ask_price,
            bid_volume=bid_volume,
            ask_volume=ask_volume,
            mid_price=mid,
            spread=spread,
            imbalance=imbalance,
            last_update=time.time(),
        )


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Snapshot complet du marché à un instant T.

    C'est cette structure qui est passée au SignalEngine à chaque tick.
    """
    symbol:       str
    candles:      Tuple[Candle, ...]     # 200 dernières bougies 1m
    micro:        MicroStructure
    timestamp:    float                  # secondes Unix


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL LAYER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class IndicatorSuite:
    """Résultat complet de tous les indicateurs calculés.
    
    Chaque indicateur expose des scores continus entre 0 et 1 :
    • 0 = aucune force
    • 1 = force maximale dans cette direction
    """
    # ── Valeurs brutes ──
    rsi:          float   # 0-100
    percent_b:    float   # 0-1, position dans les bandes de Bollinger
    atr:          float   # en points de prix
    atr_percent:  float   # atr / mid_price
    volume_surge: float   # ratio volume / moyenne
    choppiness:   float   # 0-100, tendance vs range
    engulfing:    int     # 1=bullish, -1=bearish, 0=aucun
    engulfing_quality: float  # 0-1, qualité du pattern

    # ── Scores continus (0-1) ──
    rsi_long_score:       float
    rsi_short_score:      float
    bollinger_long_score: float
    bollinger_short_score:float
    volume_surge_quality: float

    # ── Méta ──
    ready: bool            # True si assez de données pour tous les indicateurs
    regime: MarketRegime   # Régime de marché détecté
    ranging_score: float   # 0-1, 1 = marché complètement rangeant (à éviter)


@dataclass(frozen=True, slots=True)
class TradingSignal:
    """Signal de trading produit par le SignalEngine.

    Si ``action == TradeAction.WAIT``, les champs ``entry/sl/tp1/tp2`` sont à 0.
    """
    action:    TradeAction
    direction: Optional[Direction]   # None si WAIT
    score:     float                 # 0-100, score de conviction
    entry:     float                 # prix d'entrée suggéré (limit)
    sl:        float                 # stop-loss
    tp1:       float                 # take-profit 1
    tp2:       float                 # take-profit 2
    tp1_fraction: float              # part de la position clôturée à TP1 (défaut 0.6)
    reason:    str                   # message explicatif (debug)
    timestamp: float                 # secondes Unix


# ═══════════════════════════════════════════════════════════════════════════
# RISK LAYER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class PositionSize:
    """Taille de position calculée par le RiskEngine."""
    amount:          float   # en unités de base (BTC, ETH…)
    exposure_usd:    float   # amount × entry
    risk_usd:        float   # amount × |entry - sl|
    risk_pct:        float   # risk_usd / balance × 100
    kelly_fraction:  float   # fraction du Kelly utilisée
    margin_usd:      float   = 0.0  # marge engagée pour les futures


@dataclass(frozen=True, slots=True)
class ValidatedSignal:
    """Signal validé par le RiskEngine, prêt pour l'exécution."""
    signal:       TradingSignal
    position:     PositionSize
    reason:       str      # "Validation OK" ou raison du rejet
    is_valid:     bool


# ═══════════════════════════════════════════════════════════════════════════
# EXECUTION LAYER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Demande d'ordre envoyée à l'ExecutionEngine."""
    client_id:   str
    symbol:      str
    side:        str               # 'buy' ou 'sell'
    order_type:  str               # 'limit' ou 'market'
    amount:      float
    price:       float             # 0 si market
    sl:          float
    tp1:         float
    tp2:         float
    tp1_fraction: float
    reduce_only: bool = False
    created_at:  float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class OrderAck:
    """Accusé de réception d'un ordre par l'exchange."""
    client_id:     str
    exchange_id:   str    # id retourné par l'exchange
    status:        OrderStatus
    filled_amount: float
    avg_price:     float
    fees:          float  # en USDT


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """Rapport d'un trade clôturé (une ou plusieurs sorties)."""
    client_id:   str
    symbol:      str
    side:        str          # 'buy' (LONG) ou 'sell' (SHORT)
    entry:       float        # prix d'entrée moyen
    exit:        float        # prix de sortie moyen pondéré
    amount:      float        # quantité totale tradée
    pnl:         float        # profit net après frais
    pnl_percent: float        # en %
    is_winner:   bool
    reason:      ExitReason   # raison principale de la clôture
    timestamp:   float


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING LAYER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Statut de santé d'un composant."""
    component:   str          # nom du composant (ex: 'DataStream')
    status:      ComponentStatus
    message:     str          # description
    since:       float        # timestamp du dernier status change
    metrics:     dict = field(default_factory=dict)  # métriques additionnelles


@dataclass
class BotStats:
    """Statistiques globales du bot (mutable, mises à jour en direct)."""
    signals_generated: int   = 0
    trades_executed:   int   = 0
    win_count:         int   = 0
    loss_count:        int   = 0
    total_pnl:         float = 0.0
    daily_pnl:         float = 0.0
    max_drawdown:      float = 0.0
    uptime_seconds:    float = 0.0