from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TARGET_OPERATION_MODES
from .coordinator import EnergyDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EnergyDataCoordinator = hass.data[DOMAIN][config.entry_id][
        "coordinator"
    ]
    if not coordinator.data:
        _LOGGER.warning("No energy data available at setup; skipping sensor creation")
        return

    entities: list[EnergySensor] = []
    for system_id, payload in coordinator.data.items():
        for operation_mode in TARGET_OPERATION_MODES:
            entities.append(
                EnergySensor(
                    system_id=system_id,
                    operation_mode=operation_mode,
                    coordinator=coordinator,
                )
            )

    async_add_entities(entities)


class EnergySensor(CoordinatorEntity, SensorEntity):
    coordinator: EnergyDataCoordinator
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR

    def __init__(
        self,
        system_id: str,
        operation_mode: str,
        coordinator: EnergyDataCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._system_id = system_id
        self._operation_mode = operation_mode

    @property
    def _payload(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._system_id)

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self._system_id}_{self._operation_mode.lower()}"

    @property
    def name(self) -> str:
        payload = self._payload
        home_name = payload["home_name"] if payload else self._system_id
        mode_label = self._operation_mode.replace("_", " ").title()
        return f"{home_name} {mode_label}"

    @property
    def native_value(self) -> float | None:
        payload = self._payload
        if payload is None:
            return None
        return payload["energy"].get(self._operation_mode)

    @property
    def device_info(self) -> DeviceInfo | None:
        payload = self._payload
        if payload is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, self._system_id)},
            name=payload["home_name"],
            manufacturer=payload.get("manufacturer"),
            model=payload.get("model"),
        )
