# Implementation Plan: Epic 01 — Read-only MyVaillant Energy Sensors HA Integration

**Domain:** `mypyllant_readonly`  
**Target path:** `custom/custom_components/mypyllant_readonly/`  
**Library:** `myPyllant==0.9.10` (verified from `original/custom_components/mypyllant/manifest.json`)  
**Status:** Plan — no application code

---

## Table of Contents

1. [File Tree](#1-file-tree)
2. [Per-File Specifications](#2-per-file-specifications)
3. [Data Flow](#3-data-flow)
4. [API Call Details](#4-api-call-details)
5. [Entity Metadata Table](#5-entity-metadata-table)
6. [Error Handling Matrix](#6-error-handling-matrix)
7. [Acceptance Criteria Checklist](#7-acceptance-criteria-checklist)

---

## 1. File Tree

```
custom/
└── custom_components/
    └── mypyllant_readonly/
        ├── __init__.py          # Entry-point: async_setup_entry / async_unload_entry
        ├── manifest.json        # HA integration metadata + pypi requirement
        ├── config_flow.py       # UI config flow + options flow + reauth flow
        ├── coordinator.py       # EnergyDataCoordinator (DataUpdateCoordinator subclass)
        ├── sensor.py            # EnergySensor entity + async_setup_entry
        ├── const.py             # DOMAIN, option keys, defaults, interval constants
        ├── strings.json         # Translation key stubs (references common keys)
        └── translations/
            └── en.json          # English translation strings
```

Total: **8 files**.  No other platforms, no services file, no binary_sensor, no climate, no switch, no number, no datetime, no calendar, no water_heater.

---

## 2. Per-File Specifications

---

### 2.1 `const.py`

**Purpose:** Central location for the domain constant, all option key strings, and numeric defaults — keeps other modules free of magic strings.

#### Constants to define

```python
DOMAIN = "mypyllant_readonly"

# Option / entry.data keys  (string values match what MyPyllantAPI constructor expects as kwargs)
OPTION_BRAND   = "brand"    # matches MyPyllantAPI kwarg name
OPTION_COUNTRY = "country"  # matches MyPyllantAPI kwarg name

# Update interval option (stored in entry.options)
OPTION_UPDATE_INTERVAL = "update_interval"

# Defaults
DEFAULT_UPDATE_INTERVAL  = 30 * 60   # 1800 s — verified from original const.py
DEFAULT_COUNTRY          = "germany" # verified from original const.py
QUOTA_PAUSE_INTERVAL     = 3 * 3600  # 10800 s — verified from original const.py
API_DOWN_PAUSE_INTERVAL  = 15 * 60   # 900 s  — verified from original const.py

# Operation mode strings (must match DeviceData.operation_mode values exactly)
OM_HEATING          = "HEATING"
OM_DOMESTIC_HOT_WATER = "DOMESTIC_HOT_WATER"
OM_COOLING          = "COOLING"

TARGET_OPERATION_MODES = [OM_HEATING, OM_DOMESTIC_HOT_WATER, OM_COOLING]
```

#### Imports needed

```python
# No library imports needed in const.py — only plain Python values
```

#### Logic notes

- `OPTION_BRAND` and `OPTION_COUNTRY` intentionally match the `MyPyllantAPI` constructor kwarg names (`brand=`, `country=`) so that `MyPyllantAPI(**entry.data)` can be used during config-flow validation (as the original does in `validate_input`).
- `TARGET_OPERATION_MODES` drives both sensor creation and per-update lookup; centralising it here means adding or removing an operation mode in the future requires a single-line change.

---

### 2.2 `manifest.json`

**Purpose:** Declares HA integration metadata; determines how HA loads the integration, what Python package is required, and how it appears in the UI.

#### Exact content

```json
{
  "domain": "mypyllant_readonly",
  "name": "myVAILLANT Energy (Read-only)",
  "config_flow": true,
  "integration_type": "hub",
  "iot_class": "cloud_polling",
  "loggers": ["myPyllant"],
  "requirements": ["myPyllant==0.9.10"],
  "version": "1.0.0"
}
```

#### Verification notes

- `"requirements": ["myPyllant==0.9.10"]` — package name and version verified verbatim from `original/custom_components/mypyllant/manifest.json`.
- `"domain": "mypyllant_readonly"` — does not conflict with the existing `"mypyllant"` domain; both can coexist in the same HA instance.
- `"loggers": ["myPyllant"]` — requests HA to route the library's own logger through HA's log system (same pattern as original).
- No `"codeowners"`, `"documentation"`, or `"issue_tracker"` — those are optional and not needed for the custom component.

---

### 2.3 `__init__.py`

**Purpose:** HA entry-point that authenticates, instantiates `EnergyDataCoordinator`, runs the first data refresh, forwards setup to the `sensor` platform, and cleans up on unload.

#### Key functions

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool: ...
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool: ...
```

#### Imports

```python
from __future__ import annotations
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from myPyllant.api import MyPyllantAPI
from myPyllant.http_client import AuthenticationFailed, RealmInvalid, LoginEndpointInvalid

from .const import DOMAIN, OPTION_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
from .coordinator import EnergyDataCoordinator
```

#### `async_setup_entry` logic (step by step)

```
1.  Read credentials from entry.data:
      username = entry.data["username"]
      password = entry.data["password"]
      brand    = entry.data[OPTION_BRAND]    # "brand"
      country  = entry.data.get(OPTION_COUNTRY, DEFAULT_COUNTRY)  # "country"

2.  Read update interval from entry.options (falls back to default):
      update_interval = entry.options.get(OPTION_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

3.  Initialise hass.data storage:
      hass.data.setdefault(DOMAIN, {})
      hass.data[DOMAIN][entry.entry_id] = {}

4.  Construct and authenticate API:
      api = MyPyllantAPI(username=username, password=password, brand=brand, country=country)
      try:
          await api.login()
      except (AuthenticationFailed, LoginEndpointInvalid, RealmInvalid) as e:
          raise ConfigEntryAuthFailed from e
      # Pattern verified from original __init__.py lines 97-100.

5.  Construct and refresh coordinator:
      coordinator = EnergyDataCoordinator(
          hass, api, entry, timedelta(seconds=update_interval)
      )
      await coordinator.async_refresh()
      # async_refresh() raises ConfigEntryNotReady on UpdateFailed,
      # which HA retries automatically — no explicit try/except needed here.

6.  Store coordinator:
      hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

7.  Forward to sensor platform:
      await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])

8.  return True
```

#### `async_unload_entry` logic

```
1.  Unload sensor platform:
      unload_ok = await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])

2.  If unload succeeded, close API session and pop entry data:
      if unload_ok:
          await hass.data[DOMAIN][entry.entry_id]["coordinator"].api.aiohttp_session.close()
          # Pattern verified from original __init__.py async_unload_entry.
          hass.data[DOMAIN].pop(entry.entry_id)

3.  return unload_ok
```

#### Important logic notes

- **No services are registered** — the integration is read-only; registering services is explicitly OUT OF SCOPE.
- **No `async_migrate_entry`** — not needed for a greenfield integration with a single schema version.
- **`api.login()`** is the correct call (not `api.__aenter__()`); the async context manager is equivalent but `login()` is what `__aenter__` calls internally, and the original `__init__.py` uses `login()` directly.
- **Session lifecycle**: The `aiohttp_session` is opened by `api.login()` and must be explicitly closed in `async_unload_entry`. Failure to close it leaks file descriptors.

---

### 2.4 `coordinator.py`

**Purpose:** `EnergyDataCoordinator` — a `DataUpdateCoordinator` subclass that fetches the list of systems + their device energy data in a single `_async_update_data` call every `update_interval`.

#### TypedDict for data shape

```python
from typing import TypedDict
from myPyllant.models import DeviceData

class SystemEnergyPayload(TypedDict):
    home_name: str                      # display name for the system's home
    devices_data: list[list[DeviceData]] # outer index = device index within system.devices
                                         # inner list   = all DeviceData for that device
```

This shape is identical to `DailyDataCoordinator`'s `SystemWithDeviceData` TypedDict (verified from `original/custom_components/mypyllant/coordinator.py` line ~280+) so that the sensor layer can use the same traversal logic.

#### Class definition

```python
class EnergyDataCoordinator(DataUpdateCoordinator):
    data: dict[str, SystemEnergyPayload]  # keyed by system.id
    api: MyPyllantAPI

    def __init__(
        self,
        hass: HomeAssistant,
        api: MyPyllantAPI,
        entry: ConfigEntry,
        update_interval: timedelta | None,
    ) -> None: ...

    async def _refresh_session(self) -> None: ...
    async def _async_update_data(self) -> dict[str, SystemEnergyPayload]: ...
```

#### `__init__` body

```python
def __init__(self, hass, api, entry, update_interval):
    self.api = api
    self.entry = entry
    self._homes: list = []           # cached after first fetch to avoid redundant calls
    self._quota_hit_time: dt | None  = None
    self._quota_exc_info: BaseException | None = None
    self._quota_end_time: dt | None  = None
    super().__init__(
        hass,
        _LOGGER,
        name="myVAILLANT Energy (Read-only)",
        update_interval=update_interval,
    )
```

#### `_refresh_session` exact logic (copied from original coordinator.py lines 118–132)

```python
async def _refresh_session(self) -> None:
    if (
        self.api.oauth_session_expires is None
        or self.api.oauth_session_expires
            < dt.now(timezone.utc) + timedelta(seconds=180)
    ):
        _LOGGER.debug("Refreshing token for %s", self.api.username)
        await self.api.refresh_token()
```

#### `_async_update_data` full logic

```python
async def _async_update_data(self) -> dict[str, SystemEnergyPayload]:
    self._raise_if_quota_hit()          # blocks while quota/API-down cooldown is active
    try:
        await self._refresh_session()

        # --- Fetch homes (cached after first successful call) ---
        if not self._homes:
            self._homes = [
                h async for h in
                await self.hass.async_add_executor_job(self.api.get_homes)
            ]

        # --- Fetch systems with minimal flags (read-only, no optional data) ---
        systems = [
            s async for s in
            await self.hass.async_add_executor_job(
                self.api.get_systems,
                False,   # include_connection_status
                False,   # include_diagnostic_trouble_codes
                False,   # include_rts
                False,   # include_mpc
                False,   # include_ambisense_rooms
                False,   # include_energy_management
                False,   # include_eebus
                False,   # include_ambisense_capability
                self._homes,
            )
        ]

        # --- For each system, fetch device energy data ---
        data: dict[str, SystemEnergyPayload] = {}
        for system in systems:
            start = dt.now(system.timezone).replace(
                microsecond=0, second=0, minute=0, hour=0
            )
            end = start + timedelta(days=1)
            # Date window pattern verified from original coordinator.py lines 296–302.

            if not system.devices:
                continue

            home_name = system.home.home_name or system.home.nomenclature
            devices_data: list[list[DeviceData]] = []
            for device in system.devices:
                device_data_list = [
                    dd async for dd in
                    self.api.get_data_by_device(
                        device,
                        DeviceDataBucketResolution.HOUR,
                        start,
                        end,
                    )
                    # get_data_by_device returns async generator directly (no await);
                    # verified from original coordinator.py lines 348–354.
                ]
                devices_data.append(device_data_list)

            data[system.id] = {
                "home_name": home_name,
                "devices_data": devices_data,
            }

        return data

    except (AuthenticationFailed, LoginEndpointInvalid, RealmInvalid) as e:
        raise ConfigEntryAuthFailed from e
    except ClientResponseError as e:
        self._set_quota_and_raise(e)
        raise UpdateFailed(str(e)) from e
    except (CancelledError, TimeoutError) as e:
        self._raise_api_down(e)
        return {}   # satisfies mypy; HA never sees this value when UpdateFailed is raised
```

#### Quota / API-down helpers

These three private methods are transcribed exactly from `original/custom_components/mypyllant/coordinator.py` (verified lines 163–237). They are self-contained and carry no write-path logic:

| Method | Signature | What it does |
|--------|-----------|--------------|
| `_raise_if_quota_hit` | `(self) -> None` | Raises `UpdateFailed` if a quota or API-down cooldown is still active |
| `_set_quota_and_raise` | `(self, exc_info: ClientResponseError) -> None` | Calls `is_quota_exceeded_exception`; if True sets `_quota_hit_time` + optional `_quota_end_time` and re-raises |
| `_raise_api_down` | `(self, exc_info: CancelledError \| TimeoutError) -> None` | Sets `_quota_hit_time` / `_quota_exc_info` and raises `UpdateFailed` |

**Quota helper imports**

```python
from aiohttp import ClientResponseError
from asyncio import CancelledError
```

`is_quota_exceeded_exception` and `extract_quota_duration` must be **copied** from `original/custom_components/mypyllant/utils.py` (lines 214–244) into a local `utils.py` under `mypyllant_readonly/`, or inlined into `coordinator.py`. Do **not** import from `original/` — that path is not available in a deployed HA instance.

#### Full imports for `coordinator.py`

```python
from __future__ import annotations
import logging
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
from myPyllant.models import DeviceData

from .const import DOMAIN, QUOTA_PAUSE_INTERVAL, API_DOWN_PAUSE_INTERVAL
```

---

### 2.5 `config_flow.py`

**Purpose:** HA `ConfigFlow` subclass presenting a credential form (username, password, brand, country), plus `OptionsFlowHandler` for the update interval, plus a re-auth flow.

#### Key classes

| Class | Base | Step id(s) |
|-------|------|------------|
| `ConfigFlow` | `config_entries.ConfigFlow` | `user`, `reauth_confirm` |
| `OptionsFlowHandler` | `config_entries.OptionsFlow` | `init` |

#### `validate_input` helper (exactly as original)

```python
async def validate_input(hass: HomeAssistant, data: dict) -> str:
    """Attempt login; return normalised username."""
    async with MyPyllantAPI(**data) as api:
        await api.login()
    return data["username"].lower()
```

- `data` must have keys `"username"`, `"password"`, `"brand"`, `"country"`.
- Uses async context manager (`async with`) which is safe here because `validate_input` is a short-lived one-shot call — no persistent session is needed.
- Raises `AuthenticationFailed`, `RealmInvalid`, or `LoginEndpointInvalid` on failure.
- Verified from original `config_flow.py` lines 171–174.

#### `DATA_SCHEMA`

```python
from myPyllant.const import BRANDS, COUNTRIES, DEFAULT_BRAND

_COUNTRIES_OPTIONS = [
    selector.SelectOptionDict(value=k, label=v)
    for k, v in COUNTRIES[DEFAULT_BRAND].items()
]
_BRANDS_OPTIONS = [
    selector.SelectOptionDict(value=k, label=v) for k, v in BRANDS.items()
]

DATA_SCHEMA = vol.Schema({
    vol.Required("username"): str,
    vol.Required("password"): str,
    vol.Required(OPTION_BRAND, default=DEFAULT_BRAND): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=_BRANDS_OPTIONS,
            mode=selector.SelectSelectorMode.LIST,
        ),
    ),
    vol.Optional(OPTION_COUNTRY): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=_COUNTRIES_OPTIONS,
            mode=selector.SelectSelectorMode.DROPDOWN,
        ),
    ),
})
```

- Brand defaults to `DEFAULT_BRAND` (from `myPyllant.const`, which is `"vaillant"`).
- Country is `vol.Optional` — some brands do not require it; wrong country → `RealmInvalid`.
- Schema verified against original `config_flow.py` lines 88–108.

#### `OPTIONS_SCHEMA`

```python
from homeassistant.helpers.config_validation import positive_int

OPTIONS_SCHEMA = vol.Schema({
    vol.Required(OPTION_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): positive_int,
})
```

- Deliberately minimal — only the update interval is user-configurable in this read-only integration.
- No quick-veto, holiday, refresh-delay, diagnostics-fetch, or any write-related options.

#### `ConfigFlow.async_step_user`

```python
async def async_step_user(self, user_input=None):
    errors = {}
    if user_input is not None:
        try:
            username = await validate_input(self.hass, user_input)
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=username, data=user_input)
        except AuthenticationFailed:
            errors["base"] = "authentication_failed"
        except LoginEndpointInvalid:
            errors["country"] = "login_endpoint_invalid"
        except RealmInvalid:
            errors["country"] = "realm_invalid"
        except AbortFlow:
            errors["base"] = "already_configured"
        except Exception as e:
            _LOGGER.exception("Unexpected exception", exc_info=e)
            errors["base"] = "unknown"
        if "password" in user_input:
            del user_input["password"]   # do not pre-fill password on re-show
    return self.async_show_form(
        step_id="user",
        data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
        errors=errors,
    )
```

#### `ConfigFlow.async_step_reauth` / `async_step_reauth_confirm`

```python
async def async_step_reauth(self, *args, **kwargs):
    return await self.async_step_reauth_confirm()

async def async_step_reauth_confirm(self, user_input=None):
    errors = {}
    if user_input is not None:
        try:
            username = await validate_input(self.hass, user_input)
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_mismatch(reason="wrong_account")
        except AuthenticationFailed:
            errors["base"] = "authentication_failed"
        except LoginEndpointInvalid:
            errors["country"] = "login_endpoint_invalid"
        except RealmInvalid:
            errors["country"] = "realm_invalid"
        except Exception as e:
            _LOGGER.exception("Unexpected exception", exc_info=e)
            errors["base"] = "unknown"
        else:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates=user_input,
            )
    else:
        config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        user_input = dict(config_entry.data)
    if "password" in user_input:
        del user_input["password"]
    return self.async_show_form(
        step_id="reauth_confirm",
        data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
        errors=errors,
    )
```

- Logic verified verbatim from original `config_flow.py` lines 244–283.

#### `OptionsFlowHandler`

```python
class OptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
```

#### `ConfigFlow` class-level registration

```python
class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()
```

#### Full imports for `config_flow.py`

```python
from __future__ import annotations
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult, AbortFlow
from homeassistant.helpers import selector
from homeassistant.helpers.config_validation import positive_int

from myPyllant.api import MyPyllantAPI
from myPyllant.const import BRANDS, COUNTRIES, DEFAULT_BRAND
from myPyllant.http_client import AuthenticationFailed, LoginEndpointInvalid, RealmInvalid

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    OPTION_BRAND,
    OPTION_COUNTRY,
    OPTION_UPDATE_INTERVAL,
)
```

---

### 2.6 `sensor.py`

**Purpose:** Defines `EnergySensor` (a `CoordinatorEntity` + `SensorEntity` subclass) and the `async_setup_entry` factory function that creates one sensor per `(device, operation_mode)` tuple found in the coordinator's initial data.

#### `async_setup_entry`

```python
async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EnergyDataCoordinator = hass.data[DOMAIN][config.entry_id]["coordinator"]
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
    # Deduplicate: a device may report the same operation_mode in multiple DeviceData
    # entries (different energy_types). Keep only the first occurrence per
    # (system_id, device_uuid, operation_mode) triple.
    seen: set[str] = set()
    unique_entities: list[EnergySensor] = []
    for e in entities:
        uid = e.unique_id
        if uid not in seen:
            seen.add(uid)
            unique_entities.append(e)

    async_add_entities(unique_entities)
```

**Deduplication rationale:** `api.get_data_by_device` may yield multiple `DeviceData` with the same `operation_mode` but different `energy_type` (e.g., `HEATING` with `CONSUMED_ELECTRICAL_ENERGY` and `HEAT_GENERATED`). We expose one sensor per `operation_mode`, summing is not done here — we use `total_consumption_rounded` from the first matching entry. See §4 for more detail on energy type filtering.

#### `EnergySensor` class

```python
class EnergySensor(CoordinatorEntity, SensorEntity):
    coordinator: EnergyDataCoordinator
    _attr_state_class               = SensorStateClass.TOTAL_INCREASING
    _attr_device_class              = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    # UnitOfEnergy.WATT_HOUR verified from original sensor.py DataSensor.__init__

    def __init__(
        self,
        system_id: str,
        de_index: int,
        operation_mode: str,
        coordinator: EnergyDataCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._system_id = system_id
        self._de_index  = de_index
        self._operation_mode = operation_mode
```

#### `unique_id` property

```python
@property
def unique_id(self) -> str | None:
    device = self._device
    if device is None:
        return None
    return (
        f"{DOMAIN}_{self._system_id}_{device.device_uuid}"
        f"_{self._operation_mode.lower()}"
    )
    # Pattern derived from original DataSensor.unique_id (coordinator.py line ~857).
    # Uses operation_mode (lowercase) instead of positional index so it is stable
    # across API response reordering.
```

#### `name` property

```python
@property
def name(self) -> str | None:
    device = self._device
    if device is None:
        return None
    home_name  = self.coordinator.data[self._system_id]["home_name"]
    device_name = device.name_display
    mode_label  = self._operation_mode.replace("_", " ").title()
    # Examples: "Heating", "Domestic Hot Water", "Cooling"
    return f"{home_name} {device_name} {mode_label}"
    # Pattern derived from DataSensor.name (original sensor.py lines 817–826).
```

#### `_device` helper property

```python
@property
def _device(self):
    """Return the Device object for this sensor, or None if data is unavailable."""
    dd = self._device_data
    return dd.device if dd is not None else None
```

#### `_device_data` helper property

```python
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
```

#### `native_value` property

```python
@property
def native_value(self) -> float | None:
    dd = self._device_data
    if dd is None:
        return None
    return dd.total_consumption_rounded
    # total_consumption_rounded is float | None, in Wh;
    # verified from original DataSensor.native_value (sensor.py line ~882).
```

#### `device_info` property

```python
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
    # Field names (brand_name, product_name_display, name_display, device_uuid)
    # verified from original DataSensor.device_info + id_infix + name_prefix (sensor.py).
```

#### Full imports for `sensor.py`

```python
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
```

---

### 2.7 `strings.json`

**Purpose:** Translation key stubs used by HA's UI when a locale-specific `translations/<lang>.json` is absent. Also serves as the authoritative key schema validated by HA's CI tooling.

```json
{
  "config": {
    "abort": {
      "already_configured": "There's already an entry for this user",
      "reauth_successful": "Re-authentication was successful",
      "wrong_account": "The account does not match the configured user"
    },
    "step": {
      "user": {
        "title": "Login Information",
        "description": "Same credentials as the myVAILLANT app",
        "data": {
          "username": "[%key:common::config_flow::data::email%]",
          "password": "[%key:common::config_flow::data::password%]",
          "brand": "Brand",
          "country": "Country"
        }
      },
      "reauth_confirm": {
        "title": "Re-authenticate to myVAILLANT",
        "description": "Same credentials as the myVAILLANT app",
        "data": {
          "username": "[%key:common::config_flow::data::email%]",
          "password": "[%key:common::config_flow::data::password%]",
          "brand": "Brand",
          "country": "Country"
        }
      }
    },
    "error": {
      "authentication_failed": "Authentication failed — check email, password, brand and country",
      "login_endpoint_invalid": "No login endpoint found for this brand/country combination",
      "realm_invalid": "This brand requires a country to be selected",
      "already_configured": "An entry for this user already exists",
      "wrong_account": "This account does not match the configured user",
      "unknown": "Unexpected error — check logs for details"
    }
  },
  "options": {
    "step": {
      "init": {
        "data": {
          "update_interval": "Seconds between energy data refreshes (lowering this risks quota errors)"
        }
      }
    }
  }
}
```

**Key conventions** (verified from `original/custom_components/mypyllant/strings.json`):
- Error keys in `config.error` must exactly match the string values used in `errors["base"]` and `errors["country"]` in `config_flow.py`.
- `[%key:common::config_flow::data::email%]` is a HA common-key reference — HA resolves it to the locale's standard "Email Address" label.

---

### 2.8 `translations/en.json`

**Purpose:** English-language translation strings displayed in the HA UI config flow and options flow. This is the only required locale; additional locales (`de.json`, `fr.json`, etc.) are OUT OF SCOPE for the MVP.

```json
{
  "config": {
    "abort": {
      "already_configured": "There's already an entry for this user",
      "reauth_successful": "Re-authentication was successful",
      "wrong_account": "The account does not match the configured user"
    },
    "step": {
      "user": {
        "title": "Login Information",
        "description": "Same credentials as the myVAILLANT app",
        "data": {
          "username": "Email Address",
          "password": "Password",
          "brand": "Brand",
          "country": "Country"
        }
      },
      "reauth_confirm": {
        "title": "Re-authenticate to myVAILLANT",
        "description": "Same credentials as the myVAILLANT app",
        "data": {
          "username": "Email Address",
          "password": "Password",
          "brand": "Brand",
          "country": "Country"
        }
      }
    },
    "error": {
      "authentication_failed": "Authentication failed — check email, password, brand and country",
      "login_endpoint_invalid": "No login endpoint found for this brand/country combination",
      "realm_invalid": "This brand requires a country to be selected",
      "already_configured": "An entry for this user already exists",
      "wrong_account": "This account does not match the configured user",
      "unknown": "Unexpected error — check logs for details"
    }
  },
  "options": {
    "step": {
      "init": {
        "data": {
          "update_interval": "Seconds between energy data refreshes (lowering this risks quota errors)"
        }
      }
    }
  }
}
```

---

## 3. Data Flow

```
ConfigEntry loaded by HA
         │
         ▼
async_setup_entry (mypyllant_readonly/__init__.py)
  ├── Reads entry.data: username, password, brand, country
  ├── Reads entry.options: update_interval (default 1800 s)
  ├── MyPyllantAPI(username, password, brand, country)
  ├── await api.login()  ──► AuthenticationFailed → ConfigEntryAuthFailed
  ├── EnergyDataCoordinator(hass, api, entry, timedelta(seconds=update_interval))
  └── await coordinator.async_refresh()
             │
             ▼
  EnergyDataCoordinator._async_update_data()
    ├── _raise_if_quota_hit()        # no-op on first call
    ├── _refresh_session()           # checks token expiry (< 180 s → refresh)
    ├── api.get_homes()              # async generator → list of Home objects (cached)
    ├── api.get_systems(..., homes)  # async generator → list of System objects
    └── for each system:
          start = midnight(system.timezone)
          end   = start + 1 day
          for each device in system.devices:
            api.get_data_by_device(device, HOUR, start, end)  # async generator
            → list[DeviceData]  stored as devices_data[de_index]
    └── returns dict[system_id → SystemEnergyPayload]
               │
               ▼
  coordinator.data = {
    "abc123": {
      "home_name": "My Home",
      "devices_data": [
        [                              # device 0
          DeviceData(operation_mode="HEATING", total_consumption_rounded=1234.0, ...),
          DeviceData(operation_mode="DOMESTIC_HOT_WATER", total_consumption_rounded=567.0, ...),
        ],
        [                              # device 1 (cooling unit)
          DeviceData(operation_mode="COOLING", total_consumption_rounded=89.0, ...),
        ],
      ]
    }
  }
               │
               ▼
  HA forwards setup to sensor platform
               │
               ▼
  async_setup_entry (mypyllant_readonly/sensor.py)
    Iterates coordinator.data:
      For "abc123" / de_index=0 / operation_mode="HEATING"  → EnergySensor A
      For "abc123" / de_index=0 / operation_mode="DOMESTIC_HOT_WATER" → EnergySensor B
      For "abc123" / de_index=1 / operation_mode="COOLING"  → EnergySensor C
    async_add_entities([A, B, C])
               │
               ▼
  HA polls every update_interval (default 1800 s)
    → coordinator._async_update_data() runs again
    → coordinator.data updated
    → CoordinatorEntity._handle_coordinator_update() called for each sensor
    → sensor.native_value re-read → new state pushed to HA state machine
               │
               ▼
  EnergySensor.native_value
    ↪ _device_data: coordinator.data[system_id]["devices_data"][de_index]
                     → next(dd for dd if dd.operation_mode == self._operation_mode)
    ↪ returns dd.total_consumption_rounded  (Wh, float | None)
```

---

## 4. API Call Details

### 4.1 Getting the list of devices

There is no top-level "list devices" API call. Devices are properties of a `System` object:

```
System.devices  →  list[Device]
```

The sequence is:
1. `api.get_homes()` → async generator of `Home` objects (cache result in `self._homes`).
2. `api.get_systems(homes=self._homes)` → async generator of `System` objects. Each `System` already contains its `devices` list populated by the library.
3. Iterate `system.devices` — no additional call needed.

### 4.2 Resolution enum

```python
from myPyllant.enums import DeviceDataBucketResolution

DeviceDataBucketResolution.HOUR
```

`HOUR` is used (not `DAY`) because hourly buckets give finer granularity and `total_consumption_rounded` on the `DeviceData` object already sums all returned buckets for the requested window. Using `HOUR` for a same-day window is consistent with `DailyDataCoordinator` in the original (verified from `coordinator.py` line 349).

### 4.3 Date range construction

```python
from datetime import datetime as dt, timedelta

start = dt.now(system.timezone).replace(
    microsecond=0, second=0, minute=0, hour=0
)
# → today midnight in the system's local timezone
# system.timezone is a tzinfo-compatible object from the System model

end = start + timedelta(days=1)
# → tomorrow midnight in the system's local timezone
```

**Timezone note:** `system.timezone` is populated by the library from the system's configuration; it is the same timezone the Vaillant cloud uses for day boundaries. Using `dt.now(system.timezone)` ensures the "today" window matches what the cloud considers today regardless of the HA server's OS timezone. Pattern verified verbatim from `original/custom_components/mypyllant/coordinator.py` lines 296–299.

### 4.4 The `api.get_data_by_device` call

```python
device_data_gen = self.api.get_data_by_device(
    device,                          # myPyllant.models.Device
    DeviceDataBucketResolution.HOUR, # resolution
    start,                           # datetime(tz=system.timezone), today midnight
    end,                             # datetime(tz=system.timezone), tomorrow midnight
)
# get_data_by_device returns an async generator directly (no await required).
# Verified from original coordinator.py line 348: no await before api.get_data_by_device.

device_data_list = [dd async for dd in device_data_gen]
# Each dd is a myPyllant.models.DeviceData object.
```

### 4.5 Filtering to target operation modes and summing

`api.get_data_by_device` yields one `DeviceData` per (operation_mode, energy_type) combination. A single device may yield several entries with `operation_mode == "HEATING"` but different `energy_type` values (e.g., `"CONSUMED_ELECTRICAL_ENERGY"`, `"HEAT_GENERATED"`, `"CONSUMED_PRIMARY_ENERGY"`).

**Decision for MVP:** use the **first** matching `DeviceData` per `operation_mode`. This entry's `total_consumption_rounded` (in Wh) is what HA reports. The `energy_type` is treated as an internal detail not surfaced to the user.

```python
# In EnergySensor._device_data:
return next(
    (dd for dd in device_data_list if dd.operation_mode == self._operation_mode),
    None,
)

# In EnergySensor.native_value:
return dd.total_consumption_rounded   # float Wh or None
```

If a more specific `energy_type` filter (e.g. only `"CONSUMED_ELECTRICAL_ENERGY"`) is desired in a future iteration, it can be added to `_device_data` without changing the public API of the sensor.

**`total_consumption_rounded` semantics:** The library pre-sums all hourly buckets within the requested window and rounds the result. It represents today's cumulative energy consumption in **Wh** from midnight to the last completed bucket. Value is `None` when no bucket data is available (device offline or no metering).

---

## 5. Entity Metadata Table

| Entity | `unique_id` | `name` (example) | `device_class` | `state_class` | `unit` | `icon` |
|--------|-------------|------------------|----------------|---------------|--------|--------|
| Heating | `mypyllant_readonly_{system_id}_{device_uuid}_heating` | `My Home Boiler Name Heating` | `SensorDeviceClass.ENERGY` | `SensorStateClass.TOTAL_INCREASING` | `UnitOfEnergy.WATT_HOUR` | _(default HA energy icon)_ |
| Domestic Hot Water | `mypyllant_readonly_{system_id}_{device_uuid}_domestic_hot_water` | `My Home Boiler Name Domestic Hot Water` | `SensorDeviceClass.ENERGY` | `SensorStateClass.TOTAL_INCREASING` | `UnitOfEnergy.WATT_HOUR` | _(default HA energy icon)_ |
| Cooling | `mypyllant_readonly_{system_id}_{device_uuid}_cooling` | `My Home Boiler Name Cooling` | `SensorDeviceClass.ENERGY` | `SensorStateClass.TOTAL_INCREASING` | `UnitOfEnergy.WATT_HOUR` | _(default HA energy icon)_ |

**Notes:**
- `{system_id}` is the string ID returned by the API for the system (e.g. a UUID-like string).
- `{device_uuid}` is `Device.device_uuid` (string).
- `unique_id` uses lowercase `operation_mode` to be human-readable and stable.
- No custom `_attr_icon` is set — `SensorDeviceClass.ENERGY` maps to `mdi:lightning-bolt` by default in HA, which is appropriate.
- `UnitOfEnergy.WATT_HOUR` (Wh) is used. HA's Energy dashboard accepts Wh natively and converts to kWh for display. `total_consumption_rounded` returns Wh (verified from original sensor.py).
- `TOTAL_INCREASING` is correct: values accumulate from zero at midnight and reset the next day. HA recognises a value drop as a counter reset and does not treat it as negative energy.
- `last_reset` is deliberately **not set** — this pattern is deprecated for `TOTAL_INCREASING` since HA 2022.04.

---

## 6. Error Handling Matrix

| Scenario | When it occurs | Correct response | HA user experience |
|----------|---------------|------------------|--------------------|
| `AuthenticationFailed` during `async_setup_entry` | Wrong username/password, account locked | `raise ConfigEntryAuthFailed from e` (wraps original exception) | HA shows "Integration requires re-authentication", prompts re-auth flow |
| `RealmInvalid` during `async_setup_entry` | Wrong brand/country combination | `raise ConfigEntryAuthFailed from e` | Same — re-auth flow |
| `LoginEndpointInvalid` during `async_setup_entry` | Brand has no known OAuth endpoint for the country | `raise ConfigEntryAuthFailed from e` | Same — re-auth flow |
| `AuthenticationFailed` during coordinator update | Token expired and `refresh_token()` fails | `raise ConfigEntryAuthFailed from e` (in `_async_update_data` except block) | HA triggers re-auth flow; entities show "unavailable" until re-auth |
| `ClientResponseError` with "quota exceeded" body | API call volume limit reached | `_set_quota_and_raise(e)` → `UpdateFailed` | Entities show "unavailable"; polling paused for `QUOTA_PAUSE_INTERVAL` (3 h) or until API-supplied end time |
| `ClientResponseError` non-quota (5xx, 4xx) | Transient API error | `raise UpdateFailed(str(e)) from e` | Entities show "unavailable" for one cycle; next poll retries normally |
| `CancelledError` or `TimeoutError` | Network timeout, HA restart during request | `_raise_api_down(e)` → `UpdateFailed` | Entities show "unavailable"; polling paused for `API_DOWN_PAUSE_INTERVAL` (15 min) |
| Device missing from API response | Device removed from account or API regression | `coordinator.data` has fewer / different entries; `_device_data` returns `None` | Sensor reports `None` → HA shows "unavailable" for that entity; no crash |
| No devices in system (`system.devices` empty) | New account, no commissioned system | `if not system.devices: continue` | No entities created; integration appears configured but empty |
| `operation_mode` absent for a device | Device has no cooling unit / no DHW | `_device_data` returns `None`; sensor never created in `async_setup_entry` | Sensor simply does not appear — graceful degradation |
| `total_consumption_rounded` is `None` | Device offline, metering not available for the period | `native_value` returns `None` | Sensor state becomes "unavailable" in HA |
| Midnight rollover | Daily window resets at system midnight | New `start` / `end` on next poll; values start from near-zero | `TOTAL_INCREASING` treats the drop as a counter reset — correct, no action needed |

---

## 7. Acceptance Criteria Checklist

### Setup

- [ ] **AC-S1:** Config flow presents four fields: `username` (email), `password` (password), `brand` (select from `BRANDS`), `country` (dropdown from `COUNTRIES[DEFAULT_BRAND]`).
  - *Implementation note:* `BRANDS` and `COUNTRIES` imported from `myPyllant.const`. `_BRANDS_OPTIONS` and `_COUNTRIES_OPTIONS` built at module load time, matching original `config_flow.py` lines 83–90.

- [ ] **AC-S2:** On valid credentials, the entry is created with `unique_id = email.lower()`. A second submission with the same email raises `already_configured` and aborts.
  - *Implementation note:* `async_set_unique_id(username)` followed by `_abort_if_unique_id_configured()`.

- [ ] **AC-S3:** On `AuthenticationFailed`, the form re-displays with `errors["base"] = "authentication_failed"` and the password field is cleared (not pre-filled).
  - *Implementation note:* `del user_input["password"]` before re-showing form; verified pattern from original.

- [ ] **AC-S4:** On `RealmInvalid` or `LoginEndpointInvalid`, the form re-displays with `errors["country"]` set to the appropriate key, highlighting the country field.

- [ ] **AC-S5:** Config entry is stored with credentials in `entry.data`; no credentials appear in `entry.options`.

- [ ] **AC-S6:** Options flow allows changing `update_interval` (positive integer, seconds); default is `1800`.

### Entities

- [ ] **AC-E1:** After setup, exactly 0–3 `EnergySensor` entities are created per device (0 if no target `operation_mode` data; 1–3 depending on what the device reports).
  - *Implementation note:* `async_setup_entry` in `sensor.py` iterates `coordinator.data` and creates one entity per distinct `(system_id, de_index, operation_mode)` triple where `operation_mode ∈ TARGET_OPERATION_MODES`.

- [ ] **AC-E2:** Each sensor has `device_class=ENERGY`, `state_class=TOTAL_INCREASING`, `unit=Wh`.
  - *Implementation note:* Set as class-level `_attr_*` attributes on `EnergySensor`.

- [ ] **AC-E3:** Sensor `unique_id` is `mypyllant_readonly_{system_id}_{device_uuid}_{operation_mode_lower}` and is stable across HA restart.
  - *Implementation note:* `device_uuid` comes from `DeviceData.device.device_uuid` (a persistent hardware ID).

- [ ] **AC-E4:** Each sensor is grouped under a `DeviceInfo` entry identified by `(DOMAIN, "{system_id}_device_{device_uuid}")`, with `manufacturer=device.brand_name` and `model=device.product_name_display`.

- [ ] **AC-E5:** Sensor `native_value` equals `DeviceData.total_consumption_rounded` (Wh, float or None).
  - *Implementation note:* No additional summing or rounding — the library already provides the summed daily total.

- [ ] **AC-E6:** No non-energy entities exist (no binary_sensor, climate, switch, number, datetime, calendar, water_heater). Only `Platform.SENSOR` is in `PLATFORMS`.

### Polling

- [ ] **AC-P1:** Coordinator polls every `update_interval` seconds (default 1800 s). Value is read from `entry.options.get(OPTION_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)` at setup time.

- [ ] **AC-P2:** Token is refreshed before each API call when `api.oauth_session_expires < now_utc + 180s`.
  - *Implementation note:* `_refresh_session()` method on coordinator, exact logic from original.

- [ ] **AC-P3:** On quota error, polling is suspended for `QUOTA_PAUSE_INTERVAL` (10800 s) or until the API-supplied replenishment time, whichever is later.
  - *Implementation note:* `_set_quota_and_raise` + `_raise_if_quota_hit` pattern from original.

- [ ] **AC-P4:** On `CancelledError` / `TimeoutError`, polling is suspended for `API_DOWN_PAUSE_INTERVAL` (900 s).

- [ ] **AC-P5:** At midnight (system timezone), the next coordinator update returns values starting near zero. `TOTAL_INCREASING` state class handles the numerical drop correctly without any coordinator-side intervention.

### Re-authentication

- [ ] **AC-R1:** When `AuthenticationFailed` is raised during a coordinator update, `ConfigEntryAuthFailed` is raised, triggering HA's re-auth UI notification.

- [ ] **AC-R2:** The re-auth flow (`async_step_reauth_confirm`) re-uses `DATA_SCHEMA`, pre-fills all fields except `password`, validates credentials via `validate_input`, and calls `async_update_reload_and_abort`.

- [ ] **AC-R3:** Re-auth with a different account email (non-matching `unique_id`) aborts with `reason="wrong_account"`.

### Read-only guarantee

- [ ] **AC-RO1:** No `hass.services.async_register` call exists anywhere in the integration.
- [ ] **AC-RO2:** No write API calls (`set_*`, `cancel_*`, `create_*`) are imported or invoked.
- [ ] **AC-RO3:** `PLATFORMS = [Platform.SENSOR]` is the only platform registered.
- [ ] **AC-RO4:** No `services.yaml` file exists under `mypyllant_readonly/`.

---

## Appendix A — Verified Source References

| Fact | Source file | Line(s) |
|------|-------------|---------|
| Package name and version `myPyllant==0.9.10` | `original/custom_components/mypyllant/manifest.json` | 15 |
| `MyPyllantAPI(username, password, brand, country)` + `await api.login()` | `original/custom_components/mypyllant/__init__.py` | 97–100 |
| `api.aiohttp_session.close()` on unload | `original/custom_components/mypyllant/__init__.py` | 211–218 |
| `QUOTA_PAUSE_INTERVAL = 3 * 3600`, `API_DOWN_PAUSE_INTERVAL = 15 * 60` | `original/custom_components/mypyllant/const.py` | 41–42 |
| `DEFAULT_UPDATE_INTERVAL = 30 * 60` | `original/custom_components/mypyllant/const.py` | 25 |
| `DEFAULT_COUNTRY = "germany"` | `original/custom_components/mypyllant/const.py` | 28 |
| `OPTION_BRAND = "brand"`, `OPTION_COUNTRY = "country"` | `original/custom_components/mypyllant/const.py` | 12–13 |
| `_refresh_session` logic (180 s threshold) | `original/custom_components/mypyllant/coordinator.py` | 118–132 |
| `DailyDataCoordinator._async_update_data` date window | `original/custom_components/mypyllant/coordinator.py` | 296–299 |
| `api.get_data_by_device(device, HOUR, start, end)` — no await, async generator | `original/custom_components/mypyllant/coordinator.py` | 348–354 |
| `SystemWithDeviceData` TypedDict shape | `original/custom_components/mypyllant/coordinator.py` | 278–282 |
| `_raise_if_quota_hit`, `_set_quota_and_raise`, `_raise_api_down` | `original/custom_components/mypyllant/coordinator.py` | 163–237 |
| `is_quota_exceeded_exception`, `extract_quota_duration` | `original/custom_components/mypyllant/utils.py` | 214–244 |
| `DataSensor.unique_id` pattern | `original/custom_components/mypyllant/sensor.py` | 857 |
| `DataSensor.native_value` = `total_consumption_rounded` | `original/custom_components/mypyllant/sensor.py` | 882 |
| `DataSensor.device_info` fields: `brand_name`, `product_name_display`, `name_display`, `device_uuid` | `original/custom_components/mypyllant/sensor.py` | 862–875 |
| `_attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR` | `original/custom_components/mypyllant/sensor.py` | 800 |
| `validate_input` using `async with MyPyllantAPI(**data)` | `original/custom_components/mypyllant/config_flow.py` | 171–174 |
| Error key strings (`authentication_failed`, `realm_invalid`, `login_endpoint_invalid`) | `original/custom_components/mypyllant/config_flow.py` | 210–224 |
| `_abort_if_unique_id_configured()`, `add_suggested_values_to_schema` usage | `original/custom_components/mypyllant/config_flow.py` | 200–242 |
| `_abort_if_unique_id_mismatch(reason="wrong_account")`, `async_update_reload_and_abort` | `original/custom_components/mypyllant/config_flow.py` | 255–271 |
| `BRANDS`, `COUNTRIES`, `DEFAULT_BRAND` from `myPyllant.const` | `original/custom_components/mypyllant/config_flow.py` | 20–24 |
| `AuthenticationFailed`, `RealmInvalid`, `LoginEndpointInvalid` from `myPyllant.http_client` | `original/custom_components/mypyllant/config_flow.py` | 16–19 |
| Translation key structure | `original/custom_components/mypyllant/strings.json` + `translations/en.json` | full file |

---

## Appendix B — Out of Scope Reminder

The following are explicitly **OUT OF SCOPE** for this implementation. Do not add them, even as stubs.

| Category | Examples |
|----------|---------|
| Write / mutation | Any `api.set_*`, `api.cancel_*`, `api.create_*` call |
| HA services | `hass.services.async_register`, any `.yaml` service definition |
| Non-sensor platforms | `BINARY_SENSOR`, `CLIMATE`, `SWITCH`, `NUMBER`, `DATETIME`, `CALENDAR`, `WATER_HEATER` |
| Non-energy sensor types | Temperature, pressure, humidity, operation-mode strings, COP/SCOP |
| System-level coordinators | A separate `SystemCoordinator`; the single `EnergyDataCoordinator` fetches everything |
| Advanced fetch flags | `fetch_rts`, `fetch_mpc`, `fetch_ambisense_rooms`, `fetch_energy_management`, `fetch_eebus` |
| Historical data | Only today's window; no week/month/year aggregation |
| Migration helpers | `async_migrate_entry` |
| Export / report services | No `SERVICE_EXPORT`, `SERVICE_REPORT`, `SERVICE_GENERATE_TEST_DATA` |
| Multiple languages | Only `translations/en.json` for MVP |
