from __future__ import annotations

import logging
import re
from asyncio import CancelledError
from datetime import timedelta, datetime as dt, timezone
from typing import TypedDict

from aiohttp import ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from myPyllant.api import MyPyllantAPI
from myPyllant.enums import DeviceDataBucketResolution
from myPyllant.http_client import AuthenticationFailed, LoginEndpointInvalid, RealmInvalid
from myPyllant.models import DeviceData  # noqa: F401 (kept for TypedDict)

from .const import DOMAIN, QUOTA_PAUSE_INTERVAL, API_DOWN_PAUSE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SystemEnergyPayload(TypedDict):
    home_name: str
    manufacturer: str | None
    model: str | None
    # Aggregated energy in Wh per operation mode across ALL physical devices
    energy: dict[str, float]


def _is_quota_exceeded_exception(exc_info: BaseException | None) -> bool:
    """Return True if the exception is a quota-exceeded ClientResponseError."""
    return (
        isinstance(exc_info, ClientResponseError)
        and 500 > exc_info.status >= 400
        and (
            "quota exceeded" in exc_info.message.lower()
            or "out of call volume quota" in exc_info.message.lower()
        )
    )


def _extract_quota_duration(exc_info: BaseException | None) -> int | None:
    """Extract seconds from an error like 'Quota will be replenished in 00:44:41.'"""
    if not isinstance(exc_info, ClientResponseError) or not _is_quota_exceeded_exception(
        exc_info
    ):
        return None
    match = re.search(
        r"Quota will be replenished in (\d{2}):(\d{2}):(\d{2})", exc_info.message
    )
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        return hours * 3600 + minutes * 60 + seconds
    return None


class EnergyDataCoordinator(DataUpdateCoordinator):
    data: dict[str, SystemEnergyPayload]
    api: MyPyllantAPI

    def __init__(
        self,
        hass: HomeAssistant,
        api: MyPyllantAPI,
        entry: ConfigEntry,
        update_interval: timedelta | None,
    ) -> None:
        self.api = api
        self.entry = entry
        self._homes: list = []
        self._quota_hit_time: dt | None = None
        self._quota_exc_info: BaseException | None = None
        self._quota_end_time: dt | None = None
        super().__init__(
            hass,
            _LOGGER,
            name="myVAILLANT Energy (Read-only)",
            update_interval=update_interval,
        )

    async def _refresh_session(self) -> None:
        if (
            self.api.oauth_session_expires is None
            or self.api.oauth_session_expires
            < dt.now(timezone.utc) + timedelta(seconds=180)
        ):
            _LOGGER.debug("Refreshing token for %s", self.api.username)
            await self.api.refresh_token()

    def _raise_if_quota_hit(self) -> None:
        if not self._quota_hit_time:
            return

        time_elapsed = (dt.now(timezone.utc) - self._quota_hit_time).seconds

        if _is_quota_exceeded_exception(self._quota_exc_info):
            _LOGGER.debug(
                "Quota was hit %ss ago on %s by %s",
                time_elapsed,
                self._quota_hit_time,
                self.__class__,
                exc_info=self._quota_exc_info,
            )
            if self._quota_end_time:
                if dt.now(timezone.utc) < self._quota_end_time:
                    remaining = (self._quota_end_time - dt.now(timezone.utc)).seconds
                    raise UpdateFailed(
                        f"{self._quota_exc_info.message} on "  # type: ignore[union-attr]
                        f"{self._quota_exc_info.request_info.real_url}, "  # type: ignore[union-attr]
                        f"skipping update for another {remaining}s"
                    ) from self._quota_exc_info
            elif time_elapsed < QUOTA_PAUSE_INTERVAL:
                raise UpdateFailed(
                    f"{self._quota_exc_info.message} on "  # type: ignore[union-attr]
                    f"{self._quota_exc_info.request_info.real_url}, "  # type: ignore[union-attr]
                    f"skipping update for another {QUOTA_PAUSE_INTERVAL - time_elapsed}s"
                ) from self._quota_exc_info
        else:
            _LOGGER.debug(
                "myVAILLANT API is down since %ss (%s)",
                time_elapsed,
                self._quota_hit_time,
                exc_info=self._quota_exc_info,
            )
            if time_elapsed < API_DOWN_PAUSE_INTERVAL:
                raise UpdateFailed(
                    f"myVAILLANT API is down, skipping update for another"
                    f" {API_DOWN_PAUSE_INTERVAL - time_elapsed}s"
                ) from self._quota_exc_info

    def _set_quota_and_raise(self, exc_info: ClientResponseError) -> None:
        if _is_quota_exceeded_exception(exc_info):
            duration = _extract_quota_duration(exc_info)
            self._quota_hit_time = dt.now(timezone.utc)
            if duration:
                self._quota_end_time = dt.now(timezone.utc) + timedelta(seconds=duration)
            self._quota_exc_info = exc_info
            self._raise_if_quota_hit()

    def _raise_api_down(self, exc_info: CancelledError | TimeoutError) -> None:
        self._quota_hit_time = dt.now(timezone.utc)
        self._quota_exc_info = exc_info
        raise UpdateFailed(
            f"myVAILLANT API is down, skipping update for another {API_DOWN_PAUSE_INTERVAL}s"
        ) from exc_info

    async def _async_update_data(self) -> dict[str, SystemEnergyPayload]:
        self._raise_if_quota_hit()
        try:
            await self._refresh_session()

            if not self._homes:
                _LOGGER.debug("Fetching homes")
                self._homes = [
                    h
                    async for h in await self.hass.async_add_executor_job(
                        self.api.get_homes
                    )
                ]

            _LOGGER.debug("Fetching systems")
            systems = [
                s
                async for s in await self.hass.async_add_executor_job(
                    self.api.get_systems,
                    False,  # include_connection_status
                    False,  # include_diagnostic_trouble_codes
                    False,  # include_rts
                    False,  # include_mpc
                    False,  # include_ambisense_rooms
                    False,  # include_energy_management
                    False,  # include_eebus
                    False,  # include_ambisense_capability
                    self._homes,
                )
            ]

            data: dict[str, SystemEnergyPayload] = {}
            for system in systems:
                if not system.devices:
                    _LOGGER.debug("No devices in system %s, skipping", system.id)
                    continue

                start = dt.now(system.timezone).replace(
                    microsecond=0, second=0, minute=0, hour=0
                )
                end = start + timedelta(days=1)
                _LOGGER.debug(
                    "Getting energy data for %s from %s to %s", system.id, start, end
                )

                home_name = system.home.home_name or system.home.nomenclature
                first_device = system.devices[0]
                manufacturer = getattr(first_device, "brand_name", None)
                model = getattr(first_device, "product_name_display", None)

                # Aggregate consumed electrical energy (Wh) per operation mode
                # across all physical devices.
                # Each device returns multiple DeviceData per operation_mode
                # (one per energy_type: CONSUMED_ELECTRICAL_ENERGY,
                # EARNED_ENVIRONMENT_ENERGY, HEAT_GENERATED, …).
                # We want ONLY the consumed electrical energy.
                energy: dict[str, float] = {}
                for device in system.devices:
                    device_data_list = [
                        dd
                        async for dd in self.api.get_data_by_device(
                            device,
                            DeviceDataBucketResolution.HOUR,
                            start,
                            end,
                        )
                    ]
                    for dd in device_data_list:
                        if (
                            dd.operation_mode
                            and dd.total_consumption_rounded is not None
                            and getattr(dd, "energy_type", None)
                            == "CONSUMED_ELECTRICAL_ENERGY"
                        ):
                            value = max(0.0, dd.total_consumption_rounded)
                            energy[dd.operation_mode] = (
                                energy.get(dd.operation_mode, 0.0) + value
                            )
                    _LOGGER.debug(
                        "Device %s contributed to energy totals: %s",
                        getattr(device, "device_uuid", device),
                        energy,
                    )

                data[system.id] = {
                    "home_name": home_name,
                    "manufacturer": manufacturer,
                    "model": model,
                    "energy": energy,
                }

            return data

        except (AuthenticationFailed, LoginEndpointInvalid, RealmInvalid) as e:
            raise ConfigEntryAuthFailed from e
        except ClientResponseError as e:
            self._set_quota_and_raise(e)
            raise UpdateFailed(str(e)) from e
        except (CancelledError, TimeoutError) as e:
            self._raise_api_down(e)
            return {}  # satisfies mypy; unreachable in practice
