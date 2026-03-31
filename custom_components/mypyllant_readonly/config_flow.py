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

_LOGGER = logging.getLogger(__name__)

_COUNTRIES_OPTIONS = [
    selector.SelectOptionDict(value=k, label=v)
    for k, v in COUNTRIES[DEFAULT_BRAND].items()
]
_BRANDS_OPTIONS = [
    selector.SelectOptionDict(value=k, label=v) for k, v in BRANDS.items()
]

DATA_SCHEMA = vol.Schema(
    {
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
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(OPTION_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): positive_int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict) -> str:
    """Attempt login; return normalised username."""
    async with MyPyllantAPI(**data) as api:
        await api.login()
    return data["username"].lower()


class OptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
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
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception", exc_info=e)
                errors["base"] = "unknown"
            if "password" in user_input:
                del user_input["password"]
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, *args: Any, **kwargs: Any) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
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
            except Exception as e:  # pylint: disable=broad-except
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
