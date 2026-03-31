from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from myPyllant.api import MyPyllantAPI
from myPyllant.http_client import AuthenticationFailed, RealmInvalid, LoginEndpointInvalid

from .const import (
    DEFAULT_COUNTRY,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    OPTION_BRAND,
    OPTION_COUNTRY,
    OPTION_UPDATE_INTERVAL,
)
from .coordinator import EnergyDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    username: str = entry.data.get("username")  # type: ignore
    password: str = entry.data.get("password")  # type: ignore
    brand: str = entry.data.get(OPTION_BRAND)  # type: ignore
    country: str = entry.data.get(OPTION_COUNTRY, DEFAULT_COUNTRY)
    update_interval = entry.options.get(OPTION_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    _LOGGER.debug(
        "Creating API and logging in with %s in realm %s", username, country
    )
    api = MyPyllantAPI(
        username=username, password=password, brand=brand, country=country
    )
    try:
        await api.login()
    except (AuthenticationFailed, LoginEndpointInvalid, RealmInvalid) as e:
        raise ConfigEntryAuthFailed from e

    coordinator = EnergyDataCoordinator(
        hass, api, entry, timedelta(seconds=update_interval)
    )
    _LOGGER.debug("Refreshing EnergyDataCoordinator")
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform.SENSOR]
    )
    if unload_ok:
        coordinator: EnergyDataCoordinator = hass.data[DOMAIN][entry.entry_id][
            "coordinator"
        ]
        await coordinator.api.aiohttp_session.close()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
