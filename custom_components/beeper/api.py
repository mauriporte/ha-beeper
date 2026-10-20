"""Minimal async client for the Beeper Desktop API (HTTP + live WebSocket)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=20)


class BeeperError(Exception):
    """Beeper Desktop cannot be reached or answered with an error."""


class AuthError(BeeperError):
    """The access token was refused."""


class BeeperClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method, self.url + path, headers=self._headers, timeout=TIMEOUT, **kwargs
            ) as resp:
                if resp.status == 401:
                    raise AuthError("Beeper refused the access token")
                if resp.status >= 400:
                    raise BeeperError(f"{method} {path}: HTTP {resp.status} {(await resp.text())[:200]}")
                return await resp.json(content_type=None) if resp.content_length != 0 else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise BeeperError(f"Cannot reach Beeper Desktop at {self.url}: {err}") from err

    async def info(self) -> dict:
        return await self._request("GET", "/v1/info")

    async def accounts(self) -> list[dict]:
        return await self._request("GET", "/v1/accounts")

    async def chats(self, limit: int = 50) -> list[dict]:
        data = await self._request("GET", "/v1/chats", params={"limit": limit})
        return data.get("items", [])

    async def messages(self, chat_id: str, cursor: str | None = None) -> dict:
        params = {"cursor": cursor, "direction": "before"} if cursor else None
        return await self._request("GET", f"/v1/chats/{_q(chat_id)}/messages", params=params)

    async def send(self, chat_id: str, text: str, reply_to: str | None = None) -> dict:
        body: dict[str, Any] = {"text": text}
        if reply_to:
            body["replyToMessageID"] = reply_to
        return await self._request("POST", f"/v1/chats/{_q(chat_id)}/messages", json=body)

    async def serve(self, url: str, range_header: str | None = None) -> aiohttp.ClientResponse:
        """Open a stream of an attachment (Beeper Desktop's /v1/assets/serve); the caller must release() it."""
        headers = dict(self._headers)
        if range_header:
            headers["Range"] = range_header
        try:
            resp = await self._session.get(self.url + "/v1/assets/serve", params={"url": url}, headers=headers,
                                           timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise BeeperError(f"Cannot reach Beeper Desktop at {self.url}: {err}") from err
        if resp.status == 401:
            resp.release()
            raise AuthError("Beeper refused the access token")
        return resp

    async def mark_read(self, chat_id: str) -> None:
        await self._request("POST", f"/v1/chats/{_q(chat_id)}/read", json={})

    async def listen(self, on_event: Callable[[dict], Awaitable[None] | None], stop: asyncio.Event) -> None:
        """Stream live events until `stop` is set; reconnects with backoff when Beeper goes away."""
        ws_url = self.url.replace("http", "ws", 1) + "/v1/ws"
        delay = 5
        while not stop.is_set():
            try:
                async with self._session.ws_connect(ws_url, headers=self._headers, heartbeat=30) as ws:
                    await ws.send_json({"type": "subscriptions.set", "requestID": "ha", "chatIDs": ["*"]})
                    delay = 5
                    async for msg in ws:
                        if stop.is_set():
                            break
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        result = on_event(json.loads(msg.data))
                        if asyncio.iscoroutine(result):
                            await result
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                _LOGGER.debug("Beeper WebSocket closed: %s", err)
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                delay = min(delay * 2, 300)


def _q(chat_id: str) -> str:
    return quote(chat_id, safe="")


def summarize_chat(chat: dict) -> dict:
    """The fields the dashboard card and sensors need, without participant lists and capabilities."""
    preview = chat.get("preview") or {}
    return {
        "id": chat["id"],
        "title": chat.get("title") or "?",
        "network": chat.get("network"),
        "type": chat.get("type"),
        "unread": chat.get("unreadCount", 0),
        "pinned": chat.get("isPinned", False),
        "muted": chat.get("isMuted", False),
        "archived": chat.get("isArchived", False),
        "last_activity": chat.get("lastActivity"),
        "preview": _message_text(preview) if preview else "",
        "read_only": chat.get("isReadOnly", False),
    }


ASSET_SCHEMES = ("mxc://", "localmxc://", "file://")  # what Beeper Desktop's /v1/assets/serve accepts


def summarize_message(msg: dict) -> dict:
    attachments = [a for a in (_attachment(x) for x in msg.get("attachments") or []) if a]
    return {
        "id": msg["id"],
        "sender": msg.get("senderName") or msg.get("senderID"),
        "mine": msg.get("isSender", False),
        "time": msg.get("timestamp"),
        # with attachments the card shows the media itself, so no "[image]" placeholder
        "text": (msg.get("text") or "") if attachments and not msg.get("isDeleted") else _message_text(msg),
        "status": (msg.get("sendStatus") or {}).get("status"),
        "attachments": attachments,
    }


def _attachment(a: dict) -> dict | None:
    url = a.get("srcURL") or a.get("id") or ""
    if not url.startswith(ASSET_SCHEMES):
        return None
    poster = a.get("posterImg") or ""
    size = a.get("size") or {}
    return {
        "type": a.get("type") or "unknown",  # img, video, audio, unknown
        "url": url,
        "poster": poster if poster.startswith(ASSET_SCHEMES) else None,
        "mime": a.get("mimeType"),
        "name": a.get("fileName"),
        "width": size.get("width"),
        "height": size.get("height"),
        "gif": a.get("isGif", False),
        "sticker": a.get("isSticker", False),
        "voice": a.get("isVoiceNote", False),
        "duration": a.get("duration"),
    }


def _message_text(msg: dict) -> str:
    text = msg.get("text") or ""
    if msg.get("isDeleted"):
        return "(deleted)"
    kind = msg.get("type")
    if kind and kind not in ("TEXT", "NOTICE") and not text:
        return f"[{kind.lower()}]"
    return text
