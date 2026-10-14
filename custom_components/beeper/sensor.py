"""Unread counters: one total, plus one per pinned chat."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, BeeperCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BeeperCoordinator = entry.runtime_data
    async_add_entities([BeeperUnreadTotal(coordinator, entry)])
    known: set[str] = set()

    @callback
    def add_pinned() -> None:
        # shortcut: a chat that is unpinned keeps its sensor (it just goes unavailable); delete it by hand if unwanted
        new = [c for c in coordinator.data or [] if c["pinned"] and c["id"] not in known]
        known.update(c["id"] for c in new)
        if new:
            async_add_entities([BeeperChatUnread(coordinator, entry, c["id"], c["title"]) for c in new])

    add_pinned()
    entry.async_on_unload(coordinator.async_add_listener(add_pinned))


class BeeperEntity(CoordinatorEntity[BeeperCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"
    _attr_icon = "mdi:message-badge"

    def __init__(self, coordinator: BeeperCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Beeper",
            manufacturer="Beeper",
            model="Beeper Desktop API",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://developers.beeper.com/desktop-api",
        )


class BeeperUnreadTotal(BeeperEntity):
    _attr_translation_key = "unread"

    def __init__(self, coordinator: BeeperCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_unread"

    def _unread(self) -> list[dict]:
        return [c for c in self.coordinator.data or [] if c["unread"] and not c["muted"] and not c["archived"]]

    @property
    def native_value(self) -> int:
        return sum(c["unread"] for c in self._unread())

    @property
    def extra_state_attributes(self) -> dict:
        chats = sorted(self._unread(), key=lambda c: c["last_activity"] or "", reverse=True)[:20]
        return {"chats": [{"title": c["title"], "network": c["network"], "unread": c["unread"]} for c in chats]}


class BeeperChatUnread(BeeperEntity):
    def __init__(self, coordinator: BeeperCoordinator, entry: ConfigEntry, chat_id: str, title: str) -> None:
        super().__init__(coordinator, entry)
        self._chat_id = chat_id
        self._attr_unique_id = f"{entry.entry_id}_{chat_id}"
        self._attr_name = title

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.chat(self._chat_id) is not None

    @property
    def native_value(self) -> int | None:
        chat = self.coordinator.chat(self._chat_id)
        return chat["unread"] if chat else None

    @property
    def extra_state_attributes(self) -> dict:
        chat = self.coordinator.chat(self._chat_id) or {}
        return {"chat_id": self._chat_id, "network": chat.get("network"), "last_message": chat.get("preview")}
