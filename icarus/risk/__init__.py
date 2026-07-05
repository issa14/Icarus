"""icarus.risk — Gestion du risque et sizing des positions."""

from .engine import SpotRiskController
from .futures import FuturesRiskController

RiskController = SpotRiskController