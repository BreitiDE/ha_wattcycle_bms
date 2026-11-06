"""Base entity for WattCycle BMS."""

from __future__ import annotations

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import WattcycleCoordinator


class WattcycleEntity(CoordinatorEntity[WattcycleCoordinator]):
    """Common entity setup."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WattcycleCoordinator,
        description: EntityDescription,
        unique_suffix: str | None = None,
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}-{unique_suffix or description.key}"
        self._attr_device_info = coordinator.device_info
