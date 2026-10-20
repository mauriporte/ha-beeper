"""Beeper: unread counts, new-message events, sending, and a chat card backed by Beeper Desktop's local API."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, BeeperClient, BeeperError, _message_text, summarize_chat, summarize_message

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
        # Attachment URLs the card may fetch through /api/beeper/asset: only ones from messages it has loaded,
        # so the view can't be used to read other files from the PC running Beeper Desktop (file:// URLs).
        self.assets: dict[str, str | None] = {}  # url -> MIME type from the message (Beeper serves octet-stream)

    def allow_assets(self, messages: list[dict]) -> None:
        for m in messages:
            for a in m["attachments"]:
                for url, mime in ((a["url"], a["mime"]), (a["poster"], "image/jpeg")):
                    if url:
                        self.assets.pop(url, None)
                        self.assets[url] = mime if mime and mime.split("/")[0] in ("image", "video", "audio") else None
        while len(self.assets) > 2000:
            self.assets.pop(next(iter(self.assets)))

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
                    "text": _message_text(msg),  # "[image]" etc. for media, as before
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
    card = Path(__file__).parent / "frontend" / "beeper-chat-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), False)])
    # The file's mtime in the URL makes browsers and the mobile apps fetch the card again after an update.
    mtime = await hass.async_add_executor_job(lambda: int(card.stat().st_mtime))
    add_extra_js_url(hass, f"{CARD_URL}?v={mtime}")
    for cmd in (ws_chats, ws_messages, ws_send, ws_mark_read):
        websocket_api.async_register_command(hass, cmd)
    hass.http.register_view(BeeperAssetView())

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
    messages = [summarize_message(m) for m in items if not m.get("isHidden") and m.get("type") != "REACTION"]
    _coordinator(hass).allow_assets(messages)
    connection.send_result(
        msg["id"],
        {
            "messages": messages,
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


class BeeperAssetView(HomeAssistantView):
    """Streams a message attachment (image, video, voice) from Beeper Desktop, which only listens on the PC.

    The card loads it with a signed path (auth/sign_path), since <img>/<video>/<audio> can't send a token. Admins only,
    like the WebSocket commands, and only URLs of attachments the card has been shown. Range requests pass through, so
    videos and voice notes can seek.
    """

    url = "/api/beeper/asset"
    name = "api:beeper:asset"

    async def get(self, request: web.Request) -> web.StreamResponse:
        hass = request.app["hass"]
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            raise web.HTTPForbidden
        url = request.query.get("url", "")
        try:
            coordinator = _coordinator(hass)
        except HomeAssistantError as err:
            raise web.HTTPServiceUnavailable from err
        if url not in coordinator.assets:
            raise web.HTTPNotFound
        try:
            upstream = await coordinator.client.serve(url, request.headers.get("Range"))
        except BeeperError as err:
            raise web.HTTPBadGateway(text=str(err)) from err
        try:
            if upstream.status >= 400:
                raise web.HTTPBadGateway(text=f"Beeper Desktop answered HTTP {upstream.status}")
            resp = web.StreamResponse(status=upstream.status)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if h in upstream.headers:
                    resp.headers[h] = upstream.headers[h]
            if (mime := coordinator.assets[url]) and resp.headers.get("Content-Type", "").startswith("application/octet-stream"):
                resp.headers["Content-Type"] = mime
            resp.headers["Cache-Control"] = "private, max-age=86400"
            resp.headers["X-Content-Type-Options"] = "nosniff"
            # files come from other people: an SVG or HTML one opened from HA's own origin must not run scripts there
            resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'"
            await resp.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp
        finally:
            upstream.release()
