"""Beeper: unread counts, new-message events, sending, and a chat card backed by Beeper Desktop's local API."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, BeeperClient, BeeperError, summarize_chat, summarize_message

DOMAIN = "beeper"
PLATFORMS = [Platform.SENSOR]
EVENT_MESSAGE = "beeper_message"  # incoming messages only, for automations
EVENT_UPDATE = "beeper_chat_update"  # any change in a chat, so the card can refresh
CARD_URL = "/beeper_static/beeper-chat-card.js"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_LOGGER = logging.getLogger(__name__)


class BeeperCoordinator(DataUpdateCoordinator[list[dict]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=60), config_entry=entry)
        self.client = BeeperClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
        self._stop = asyncio.Event()
        self._seen: deque[str] = deque(maxlen=500)

    async def _async_update_data(self) -> list[dict]:
        try:
            return [summarize_chat(c) for c in await self.client.chats(limit=100)]
        except AuthError as err:
            raise ConfigEntryAuthFailed from err
        except BeeperError as err:
            raise UpdateFailed(str(err)) from err

    def chat(self, chat_id: str) -> dict | None:
        return next((c for c in self.data or [] if c["id"] == chat_id), None)

    def find_chat(self, name: str) -> dict | None:
        """Exact chat ID, else a case-insensitive title match."""
        name_l = name.casefold()
        return self.chat(name) or next((c for c in self.data or [] if c["title"].casefold() == name_l), None)

    async def _on_event(self, event: dict) -> None:
        kind = event.get("type", "")
        chat_id = event.get("chatID")
        if not kind.startswith(("message.", "chat.")) or not chat_id:
            return
        self.hass.bus.async_fire(EVENT_UPDATE, {"chat_id": chat_id, "type": kind})
        await self.async_request_refresh()
        if kind != "message.upserted":
            return
        for msg in event.get("entries") or []:
            if msg.get("isSender") or msg.get("id") in self._seen:
                continue  # edits arrive as upserts too: announce each message once
            self._seen.append(msg.get("id"))
            chat = self.chat(chat_id) or {}
            m = summarize_message(msg)
            self.hass.bus.async_fire(
                EVENT_MESSAGE,
                {
                    "chat_id": chat_id,
                    "chat": chat.get("title"),
                    "network": chat.get("network"),
                    "message_id": m["id"],
                    "sender": m["sender"],
                    "text": m["text"],
                },
            )

    def start_listener(self, entry: ConfigEntry) -> None:
        entry.async_create_background_task(self.hass, self.client.listen(self._on_event, self._stop), "beeper_ws")

    def stop_listener(self) -> None:
        self._stop.set()


def _coordinator(hass: HomeAssistant) -> BeeperCoordinator:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Beeper is not loaded")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(Path(__file__).parent / "frontend" / "beeper-chat-card.js"), False)]
    )
    add_extra_js_url(hass, CARD_URL)
    for cmd in (ws_chats, ws_messages, ws_send, ws_mark_read):
        websocket_api.async_register_command(hass, cmd)

    async def send_message(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass)
        chat = coordinator.find_chat(call.data["chat"])
        if chat is None:
            raise ServiceValidationError(f"No Beeper chat with ID or title '{call.data['chat']}'")
        try:
            result = await coordinator.client.send(chat["id"], call.data["message"])
        except BeeperError as err:
            raise HomeAssistantError(str(err)) from err
        return {"chat_id": chat["id"], "pending_message_id": result.get("pendingMessageID")}

    hass.services.async_register(
        DOMAIN,
        "send_message",
        send_message,
        schema=vol.Schema({vol.Required("chat"): cv.string, vol.Required("message"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = BeeperCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.start_listener(entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data.stop_listener()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# WebSocket commands for the chat card. Admin only: they read and send in every chat.


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "beeper/chats"})
@callback
def ws_chats(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinator = _coordinator(hass)
    connection.send_result(msg["id"], {"available": coordinator.last_update_success, "chats": coordinator.data or []})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "beeper/messages", vol.Required("chat_id"): str, vol.Optional("cursor"): str}
)
@websocket_api.async_response
async def ws_messages(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    try:
        data = await _coordinator(hass).client.messages(msg["chat_id"], msg.get("cursor"))
    except BeeperError as err:
        connection.send_error(msg["id"], "beeper_error", str(err))
        return
    items = sorted(data.get("items", []), key=lambda m: m.get("sortKey") or m.get("timestamp") or "")
    connection.send_result(
        msg["id"],
        {
            "messages": [summarize_message(m) for m in items if not m.get("isHidden") and m.get("type") != "REACTION"],
            "has_more": data.get("hasMore", False),
            "cursor": data.get("oldestCursor"),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "beeper/send", vol.Required("chat_id"): str, vol.Required("text"): vol.All(str, vol.Length(min=1))}
)
@websocket_api.async_response
async def ws_send(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    try:
        result = await _coordinator(hass).client.send(msg["chat_id"], msg["text"])
    except BeeperError as err:
        connection.send_error(msg["id"], "beeper_error", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "beeper/mark_read", vol.Required("chat_id"): str})
@websocket_api.async_response
async def ws_mark_read(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinator = _coordinator(hass)
    try:
        await coordinator.client.mark_read(msg["chat_id"])
    except BeeperError as err:
        connection.send_error(msg["id"], "beeper_error", str(err))
        return
    await coordinator.async_request_refresh()
    connection.send_result(msg["id"])
