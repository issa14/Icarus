"""icarus.execution — Moteur d'exécution des ordres."""

from .engine import SpotExecutionController
from .futures import FuturesExecutionController

ExecutionController = SpotExecutionController