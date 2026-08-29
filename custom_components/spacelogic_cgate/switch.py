"""Switch platform for SpaceLogic C-Gate integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CGateConfigEntry
from .cgate import CGateClient, CGateGroup
from .const import CONF_GROUP_OVERRIDES, DEFAULT_GROUP_TYPE, DOMAIN, GROUP_TYPE_SWITCH
from .light import _match_area

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CGateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up C-Gate switches from a config entry."""
    client: CGateClient = entry.runtime_data
    area_reg = ar.async_get(hass)
    areas: dict[str, str] = {
        area.name.lower(): area.name for area in area_reg.async_list_areas()
    }
    group_overrides: dict[str, str] = entry.options.get(
        CONF_GROUP_OVERRIDES, {}
    )
    known_groups: set[str] = set()

    discovered = await client.discover_lighting_groups()
    entities = []
    for group in discovered:
        if group_overrides.get(group.unique_id, DEFAULT_GROUP_TYPE) != GROUP_TYPE_SWITCH:
            continue
        known_groups.add(group.unique_id)
        suggested_area = _match_area(group.name, areas) if group.name else None
        entities.append(CGateSwitch(client, group, entry.entry_id, suggested_area))
    if entities:
        async_add_entities(entities)

    @callback
    def _handle_status_update(group: CGateGroup) -> None:
        if group.unique_id not in known_groups:
            if group_overrides.get(group.unique_id, DEFAULT_GROUP_TYPE) != GROUP_TYPE_SWITCH:
                return
            known_groups.add(group.unique_id)
            suggested_area = _match_area(group.name, areas) if group.name else None
            async_add_entities(
                [CGateSwitch(client, group, entry.entry_id, suggested_area)]
            )

    unsub = client.register_status_callback(_handle_status_update)
    entry.async_on_unload(unsub)


class CGateSwitch(SwitchEntity):
    """Representation of a C-Bus group as a switch."""

    _attr_has_entity_name = False
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        client: CGateClient,
        group: CGateGroup,
        entry_id: str,
        suggested_area: str | None = None,
    ) -> None:
        """Initialize the switch."""
        self._client = client
        self._group = group
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{group.unique_id}"
        self._attr_name = group.name or f"Group {group.group}"
        self._suggested_area = suggested_area
        self.entity_id = f"switch.sl_group_{group.group}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._group.unique_id}")},
            name=self._attr_name,
            manufacturer="Schneider Electric",
            model="C-Bus Group",
            via_device=(DOMAIN, self._entry_id),
            suggested_area=self._suggested_area,
        )

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._group.level > 0

    @property
    def available(self) -> bool:
        """Return True if the C-Gate connection is active."""
        return self._client.connected

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await self._client.turn_on(self._group)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await self._client.turn_off(self._group)
        self.async_write_ha_state()

    @callback
    def _handle_group_update(self, group: CGateGroup) -> None:
        """Handle a status update for this group."""
        if group.unique_id == self._group.unique_id:
            self.async_write_ha_state()

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        """Refresh availability when the C-Gate link comes or goes.

        Nothing else writes this entity's state on an outage, so without it the
        entity would keep presenting a level it can no longer verify.
        """
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for status updates when entity is added."""
        self.async_on_remove(
            self._client.register_status_callback(self._handle_group_update)
        )
        self.async_on_remove(
            self._client.register_connection_callback(
                self._handle_connection_change
            )
        )

    async def async_update(self) -> None:
        """Fetch the latest level from C-Gate."""
        if self._client.connected:
            await self._client.get_level(self._group)
