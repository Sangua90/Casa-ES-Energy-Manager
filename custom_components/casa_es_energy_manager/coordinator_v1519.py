"""Casa ES Energy Manager v1.5.19 long-memory thermal demand model."""

from __future__ import annotations

from typing import Any

from .coordinator_v1517 import CasaESEnergyCoordinator as V1517Coordinator
from .thermal_learning_v1519 import ThermalLearnerV1519


class CasaESEnergyCoordinator(V1517Coordinator):
    """v1.5.19 coordinator with 30-day weighted DHW demand memory."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self.thermal_learner = ThermalLearnerV1519(hass, entry.entry_id)
