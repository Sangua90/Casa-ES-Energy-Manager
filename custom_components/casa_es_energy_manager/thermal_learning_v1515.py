"""v1.5.15 thermal learner persistence hardening."""

from __future__ import annotations

from typing import Any

from .thermal_learning_v15 import ThermalLearnerV15


class ThermalLearnerV1515(ThermalLearnerV15):
    """Persist every real thermal observation that changes learned state.

    Temperature changes are infrequent compared with the 5-second coordinator
    refresh. Saving on every learned change prevents a Home Assistant restart or
    integration reload from discarding hours of standby-loss or draw learning.
    """

    async def async_observe(self, devices: list[dict[str, Any]]) -> None:
        await super().async_observe(devices)
        if self._dirty > 0:
            await self.async_save()
