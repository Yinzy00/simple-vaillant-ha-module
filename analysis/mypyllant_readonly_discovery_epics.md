# Discovery & Epics — mypyllant_readonly Integration

**Scope:** Read-only Home Assistant custom integration (MVP)
**Integration domain:** `mypyllant_readonly`
**Workspace path:** `custom/` (currently empty)
**Original integration domain:** `mypyllant` (must not conflict)

---

## 1. MVP Scope

### In scope

| # | Item |
|---|------|
| 1 | Config-flow UI: username, password, brand (Vaillant / Saunier Duval / …), country |
| 2 | OAuth session management (token acquisition + refresh via `MyPyllantAPI`) |
| 3 | Daily energy coordinator that calls `api.get_data_by_device()` once per polling cycle |
| 4 | Three `SensorEntity` instances per device, one for each `operation_mode`: `HEATING`, `DOMESTIC_HOT_WATER`, `COOLING` |
| 5 | HA sensor platform metadata: `device_class=ENERGY`, `state_class=TOTAL_INCREASING`, `native_unit=Wh` (later convertible to kWh by HA) |
| 6 | Basic error handling: auth failure → `ConfigEntryAuthFailed`; quota/API-down → `UpdateFailed` with backoff |

### Out of scope (hard cut)

| Category | Examples |
|----------|---------|
| All write operations | Climate control, DHW mode changes, quick veto, holiday, cooling-for-days, ventilation, any `service_call` that mutates state |
| Non-energy sensor types | Temperature, pressure, humidity, operation mode strings, efficiency ratios |
| Other HA platforms | `binary_sensor`, `climate`, `switch`, `number`, `datetime`, `calendar`, `water_heater` |
| Advanced fetch flags | `fetch_rts`, `fetch_mpc`, `fetch_ambisense_rooms`, `fetch_energy_management`, `fetch_eebus`, etc. |
| System-level (non-device) data | `SystemCoordinator`, outdoor temperature, water pressure, energy manager state |
| Efficiency sensors | COP / SCOP calculations |
| Test data / export / report services | `SERVICE_GENERATE_TEST_DATA`, `SERVICE_EXPORT`, `SERVICE_REPORT` |
| Migration helpers | `async_migrate_entry` |
| Data from historical periods | Only today (midnight → now in system timezone) |

---

## 2. Tech Notes

### 2.1 Python library

| Property | Value |
|----------|-------|
| Package | `myPyllant` (PyPI) |
| Version pinned by original | `myPyllant==0.9.10` (see `manifest.json`) |
| Async API class | `myPyllant.api.MyPyllantAPI` |
| Auth exceptions | `myPyllant.http_client.AuthenticationFailed`, `RealmInvalid`, `LoginEndpointInvalid` |
| Brand/country constants | `myPyllant.const.BRANDS`, `myPyllant.const.COUNTRIES`, `myPyllant.const.DEFAULT_BRAND` |
| Enums | `myPyllant.enums.DeviceDataBucketResolution` (values: `HOUR`, `DAY`, `MONTH`) |
| Models | `myPyllant.models.System`, `myPyllant.models.Device`, `myPyllant.models.DeviceData`, `myPyllant.models.Home` |

### 2.2 Authentication flow

1. Instantiate `MyPyllantAPI(username, password, brand, country)`.
2. Call `await api.__aenter__()` (or use as an async context manager) to complete the OAuth device-flow / password-grant and obtain tokens.
3. Before each data fetch, check `api.oauth_session_expires`; if within 180 s of expiry call `await api.refresh_token()`.
4. On `AuthenticationFailed` → raise `ConfigEntryAuthFailed` so HA triggers a re-auth flow.

The brand + country parameters determine the OAuth realm URL; wrong values raise `RealmInvalid` or `LoginEndpointInvalid`.

### 2.3 Energy data API calls

#### Coordinator fetch (daily window)

```python
from datetime import datetime as dt, timedelta
from myPyllant.enums import DeviceDataBucketResolution

start = dt.now(system.timezone).replace(microsecond=0, second=0, minute=0, hour=0)
end   = start + timedelta(days=1)

# For each device in system.devices:
device_data_stream = api.get_data_by_device(
    device,                           # myPyllant.models.Device
    DeviceDataBucketResolution.HOUR,  # hourly buckets; coordinator sums them
    start,
    end,
)
device_data_list = [da async for da in device_data_stream]
```

`api.get_data_by_device()` is an **async generator** that yields `DeviceData` objects — one per (operation_mode, energy_type) combination supported by a physical device.

#### `DeviceData` fields relevant to this integration

| Field | Type | Role |
|-------|------|------|
| `operation_mode` | `str` | Energy use category — `"HEATING"`, `"DOMESTIC_HOT_WATER"`, `"COOLING"` |
| `energy_type` | `str \| None` | Sub-category (e.g. `"CONSUMED_PRIMARY_ENERGY"`); may be `None` |
| `data` | `list[DeviceDataBucket]` | Hourly energy buckets for the requested window |
| `total_consumption_rounded` | `float \| None` | Sum of today's buckets, in **Wh**, rounded |
| `skip_data_update` | `bool` | Set to `True` to skip API fetch for a disabled sensor (optimisation from original integration) |

#### Filtering to the three target metrics

Filter `device_data_list` by `operation_mode` value:

| HA sensor | `DeviceData.operation_mode` value |
|-----------|----------------------------------|
| Power usage: Heating | `"HEATING"` |
| Power usage: Sanitary hot water | `"DOMESTIC_HOT_WATER"` |
| Power usage: Cooling | `"COOLING"` |

If a device does not support a given mode (i.e. no matching `DeviceData` entry), that sensor is simply **not created** (graceful degradation).

### 2.4 HA sensor platform requirements for energy sensors

```python
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy

class MyEnergySensor(CoordinatorEntity, SensorEntity):
    _attr_device_class              = SensorDeviceClass.ENERGY
    _attr_state_class               = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR   # Wh; HA converts to kWh in Energy dashboard
```

- `TOTAL_INCREASING` is correct because values reset to zero at midnight each day (new daily window); HA treats a drop in value as a meter reset.
- Using `UnitOfEnergy.WATT_HOUR` (Wh) matches what `total_consumption_rounded` returns; HA's Energy dashboard accepts either Wh or kWh.
- Do **not** use `last_reset` with `TOTAL_INCREASING` (deprecated pattern in HA ≥ 2022.04).

### 2.5 Domain and manifest

```json
{
  "domain": "mypyllant_readonly",
  "name": "myVAILLANT Energy (Read-only)",
  "config_flow": true,
  "integration_type": "hub",
  "iot_class": "cloud_polling",
  "requirements": ["myPyllant==0.9.10"]
}
```

The domain `mypyllant_readonly` does not conflict with the existing `mypyllant` domain and can coexist in the same HA instance.

---

## 3. Epic

### Epic E-1 — Daily energy consumption sensors for Vaillant devices

**Goal**
A Home Assistant user can authenticate with the MyVaillant cloud, and immediately see today's energy consumption (heating, domestic hot water, cooling) as sensor entities in the Home Assistant dashboard and Energy panel — without any write access to their heating system.

---

### User Stories

#### US-1: Initial configuration
> **As a** Home Assistant user,
> **I want to** add the `mypyllant_readonly` integration via the UI,
> **so that** I can enter my MyVaillant credentials and start receiving energy data without any coding.

**Acceptance criteria**
- A config-flow step is presented with fields: `username` (text), `password` (password), `brand` (select, options from `myPyllant.const.BRANDS`), `country` (select, options from `myPyllant.const.COUNTRIES[brand]`).
- On success, the entry is created and persisted; HA shows it as "Configured".
- On `AuthenticationFailed`, the form re-displays with a translated error message.
- On `RealmInvalid` / `LoginEndpointInvalid`, the form re-displays with a distinct error.
- Credentials are stored in `entry.data`; all other config is in `entry.options`.
- Duplicate entries for the same username are blocked by `_abort_if_unique_id_configured`.

#### US-2: Energy sensors are created at startup
> **As a** Home Assistant user,
> **I want to** see three energy sensors per Vaillant device automatically after setup,
> **so that** I can add them to my Energy dashboard without manual entity configuration.

**Acceptance criteria**
- After a successful config entry load, up to three `SensorEntity` instances are registered per discovered device:
  - `{home_name} {device_name} Heating` (operation_mode = `"HEATING"`)
  - `{home_name} {device_name} Domestic Hot Water` (operation_mode = `"DOMESTIC_HOT_WATER"`)
  - `{home_name} {device_name} Cooling` (operation_mode = `"COOLING"`)
- Each sensor reports `device_class=ENERGY`, `state_class=TOTAL_INCREASING`, unit `Wh`.
- A sensor whose operation_mode is absent from the device's `DeviceData` list is silently not created.
- Each sensor is associated with a `DeviceInfo` (manufacturer, model, identifiers).
- Sensor `unique_id` is stable across restarts (based on `system_id` + `device_uuid` + `operation_mode`).

#### US-3: Periodic data refresh
> **As a** Home Assistant user,
> **I want** the energy values to update automatically throughout the day,
> **so that** my Energy dashboard reflects near-real-time consumption.

**Acceptance criteria**
- A `DataUpdateCoordinator` polls `api.get_data_by_device()` on a configurable interval (default: `DEFAULT_UPDATE_INTERVAL`, suggested 30 min).
- Token is refreshed before each fetch if it expires within 180 s.
- On quota-exceeded (`ClientResponseError` with "Quota Exceeded" body), polling is paused for `QUOTA_PAUSE_INTERVAL` (3 h); `UpdateFailed` is raised; entities show "unavailable" in HA.
- On timeout / cancelled-error, polling is paused for `API_DOWN_PAUSE_INTERVAL` (15 min).
- At midnight (system timezone), the coordinator's next successful fetch returns values starting from zero (new daily window); `TOTAL_INCREASING` state class handles the drop correctly.

#### US-4: Re-authentication after session expiry
> **As a** Home Assistant user,
> **I want** the integration to prompt me to re-enter credentials if my session expires,
> **so that** I don't have to remove and re-add the integration manually.

**Acceptance criteria**
- When `AuthenticationFailed` is raised during a coordinator update, `ConfigEntryAuthFailed` is raised (triggering HA's built-in re-auth flow).
- The re-auth flow reuses the `config_flow` step with the same schema (pre-filled username, new password).

---

### Acceptance Criteria (Epic-level)

1. The `custom/` folder contains a complete integration skeleton: `manifest.json`, `__init__.py`, `config_flow.py`, `coordinator.py`, `sensor.py`, `const.py`, `strings.json`, `translations/en.json`.
2. The integration domain is `mypyllant_readonly`; it does not register any platform other than `sensor`.
3. No service is registered; no write method of `MyPyllantAPI` is called anywhere in the codebase.
4. All three target `operation_mode` values (`HEATING`, `DOMESTIC_HOT_WATER`, `COOLING`) are handled; unknown modes are ignored without crashing.
5. Integration loads cleanly in a HA dev environment (`hass` check) with no import errors.
6. A newly added config entry results in sensor entities visible in HA's entity registry within one polling cycle.
7. Removing the config entry cleans up all created entities (standard HA `async_unload_entry` pattern).

---

### Edge Cases

| # | Scenario | Expected behaviour |
|---|----------|--------------------|
| EC-1 | Device has no `COOLING` data (e.g. heating-only boiler) | `COOLING` sensor is not created; no error; other sensors unaffected |
| EC-2 | `total_consumption_rounded` returns `None` (mid-day fetch before first hourly bucket) | `native_value` returns `None`; sensor shows "unknown" in HA |
| EC-3 | System has zero devices (`system.devices == []`) | No sensors created for that system; coordinator logs a debug message and continues |
| EC-4 | Multiple systems under one account | Sensors created per device per system; `unique_id` includes `system_id` to prevent collisions |
| EC-5 | Multiple devices per system | Each device gets its own sensor set; name collision avoided by `device_uuid` in `unique_id` |
| EC-6 | API returns `DeviceData` entries with duplicate `operation_mode` | Only the first matching entry is used (same behaviour as original `de_index`/`da_index` pattern) |
| EC-7 | User configures the wrong country for their brand | `RealmInvalid` is caught in config flow; localised error message shown; entry not created |
| EC-8 | HA restarts while API is down | Coordinator raises `UpdateFailed` on first refresh; entities show "unavailable"; auto-recovers when API is reachable |
| EC-9 | User already has the full `mypyllant` integration installed | Both entries coexist; no `unique_id` collision because domains differ |
| EC-10 | Daytime restart (midnight boundary crossed) | Value drops detected by HA via `TOTAL_INCREASING`; treated as meter reset; no manual `last_reset` needed |

---

### Dependencies

#### Library

| Dependency | Version | Why |
|------------|---------|-----|
| `myPyllant` | `==0.9.10` | Provides `MyPyllantAPI`, `DeviceData`, `DeviceDataBucketResolution`, auth exceptions |
| `aiohttp` | (transitive via `myPyllant`) | Async HTTP client; `ClientResponseError` used for quota detection |

#### Home Assistant platform requirements

| Requirement | Min HA version | Notes |
|-------------|---------------|-------|
| `homeassistant.components.sensor.SensorDeviceClass.ENERGY` | 2022.5 | Required for Energy dashboard |
| `homeassistant.components.sensor.SensorStateClass.TOTAL_INCREASING` | 2021.9 | Replaces deprecated `last_reset` pattern |
| `homeassistant.const.UnitOfEnergy` | 2022.11 | Replaces string literal `"Wh"` |
| `homeassistant.helpers.update_coordinator.DataUpdateCoordinator` | 2021.1 | Polling coordinator |
| `homeassistant.exceptions.ConfigEntryAuthFailed` | 2021.6 | Triggers re-auth flow automatically |
| Config-flow (`config_flow: true` in `manifest.json`) | 2020.12 | UI-based setup; no `configuration.yaml` required |

#### Dev / CI dependencies (not shipped)

- `pytest-homeassistant-custom-component` — unit test harness
- `myPyllant.tests.generate_test_data` — fixture generation (read-only use in tests only)

---

## 4. Open Questions

| # | Question | Impact if unresolved |
|---|----------|----------------------|
| OQ-1 | Does `myPyllant.api.get_data_by_device()` guarantee that `operation_mode` values are always uppercase strings (`"HEATING"`, `"DOMESTIC_HOT_WATER"`, `"COOLING"`), or can they vary by brand/firmware? | Sensor creation logic depends on exact string matching; may need normalisation |
| OQ-2 | Is `DeviceData.total_consumption_rounded` always in **Wh**, or does it vary by device/API version? | Unit declaration on the sensor entity depends on this |
| OQ-3 | Should the polling interval be user-configurable via an options flow, or is a hardcoded 30-minute default acceptable for MVP? | Affects config_flow scope; low risk if deferred post-MVP |
| OQ-4 | Is pinning to `myPyllant==0.9.10` appropriate, or should the MVP track the latest release? | Version drift may change API shape or add/remove `operation_mode` values |

---

## 5. File Structure (target, `custom/` folder)

```
custom/
└── custom_components/
    └── mypyllant_readonly/
        ├── __init__.py          # async_setup_entry, async_unload_entry
        ├── config_flow.py       # ConfigFlow (username/password/brand/country)
        ├── coordinator.py       # DailyEnergyCoordinator (single coordinator)
        ├── sensor.py            # EnergyDataSensor (three instances per device)
        ├── const.py             # DOMAIN, option keys, defaults
        ├── manifest.json        # domain, requirements, iot_class
        ├── strings.json         # UI string keys
        └── translations/
            └── en.json          # English UI strings
```

No other files are needed for the MVP.

---

**Status: Ready for Planning**
