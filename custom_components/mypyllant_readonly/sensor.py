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
        for de_index, device_data_list in enumerate(payload["devices_data"]):
            for dd in device_data_list:
                if dd.operation_mode in TARGET_OPERATION_MODES:
                    entities.append(
                        EnergySensor(
                            system_id=system_id,
                            de_index=de_index,
                            operation_mode=dd.operation_mode,
                            coordinator=coordinator,
                        )
                    )

    # Deduplicate: keep only the first occurrence per (system_id, device_uuid, operation_mode)
    seen: set[str] = set()
    unique_entities: list[EnergySensor] = []
    for e in entities:
        uid = e.unique_id
        if uid not in seen:
            seen.add(uid)
            unique_entities.append(e)

    async_add_entities(unique_entities)


class EnergySensor(CoordinatorEntity, SensorEntity):
    coordinator: EnergyDataCoordinator
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR

    def __init__(
        self,
        system_id: str,
        de_index: int,
        operation_mode: str,
        coordinator: EnergyDataCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._system_id = system_id
        self._de_index = de_index
        self._operation_mode = operation_mode

    @property
    def _device_data(self):
        """Return the matching DeviceData for this sensor's operation_mode, or None."""
        if not self.coordinator.data:
            return None
        payload = self.coordinator.data.get(self._system_id)
        if not payload or len(payload["devices_data"]) <= self._de_index:
            return None
        device_data_list = payload["devices_data"][self._de_index]
        return next(
            (dd for dd in device_data_list if dd.operation_mode == self._operation_mode),
            None,
        )

    @property
    def _device(self):
        """Return the Device object for this sensor, or None if data is unavailable."""
        dd = self._device_data
        return dd.device if dd is not None else None

    @property
    def unique_id(self) -> str | None:
        device = self._device
        if device is None:
            return None
        return (
            f"{DOMAIN}_{self._system_id}_{device.device_uuid}"
            f"_{self._operation_mode.lower()}"
        )

    @property
    def name(self) -> str | None:
        device = self._device
        if device is None:
            return None
        home_name = self.coordinator.data[self._system_id]["home_name"]
        device_name = device.name_display
        mode_label = self._operation_mode.replace("_", " ").title()
        return f"{home_name} {device_name} {mode_label}"

    @property
    def native_value(self) -> float | None:
        dd = self._device_data
        if dd is None:
            return None
        return dd.total_consumption_rounded

    @property
    def device_info(self) -> DeviceInfo | None:
        device = self._device
        if device is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._system_id}_device_{device.device_uuid}")},
            name=f"{self.coordinator.data[self._system_id]['home_name']} {device.name_display}",
            manufacturer=device.brand_name,
            model=device.product_name_display,
        )
