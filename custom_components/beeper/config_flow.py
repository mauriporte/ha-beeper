from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN
from .api import AuthError, BeeperClient, BeeperError


def _schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_URL): str, vol.Required(CONF_TOKEN): str})


async def _validate(hass, url: str, token: str) -> str | None:
    client = BeeperClient(async_get_clientsession(hass), url, token)
    try:
        await client.accounts()  # needs a valid token, unlike /v1/info
    except AuthError:
        return "invalid_auth"
    except BeeperError:
        return "cannot_connect"
    return None


class BeeperConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not (error := await _validate(self.hass, user_input[CONF_URL], user_input[CONF_TOKEN])):
                await self.async_set_unique_id(user_input[CONF_URL].rstrip("/").lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Beeper", data=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="user", data_schema=_schema(), errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            data = {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
            if not (error := await _validate(self.hass, data[CONF_URL], data[CONF_TOKEN])):
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}), errors=errors
        )
