"""v1.5 adaptive learning guards.

Manual user operation and internal appliance overrides must never teach the
automatic Casa ES model. The physical watts still remain visible to the global
energy balance; they are simply excluded from learning.
"""

from __future__ import annotations

from typing import Any

from .adaptive_learning import AdaptivePowerLearner
from .const import DEVICE_MODE_AUTO


class AdaptivePowerLearnerV15(AdaptivePowerLearner):
    """Adaptive learner that accepts only Casa ES automatic observations."""

    async def async_observe(self, devices: list[dict[str, Any]]) -> None:
        automatic = [
            item
            for item in devices
            if str(item.get("management_mode") or DEVICE_MODE_AUTO) == DEVICE_MODE_AUTO
            and str(item.get("device_type") or "") != "thermal_storage"
            and not bool(item.get("learning_excluded"))
        ]
        await super().async_observe(automatic)
