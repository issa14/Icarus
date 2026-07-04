"""icarus.data — Couche d'accès aux données de marché temps réel.

Fournit le buffer OHLCV thread-safe et le DataStream WebSocket
(implémentation de DataProvider pour Binance).
"""

from icarus.data.buffer import OhlcvBuffer
from icarus.data.stream import BinanceDataProvider