"""Light platform for SpaceLogic C-Gate integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CGateConfigEntry
from .cgate import CGateClient, CGateGroup
from .const import (
    CBUS_LEVEL_MAX,
    CONF_GROUP_OVERRIDES,
    DEFAULT_GROUP_TYPE,
    DOMAIN,
    GROUP_TYPE_DIMMER,
    GROUP_TYPE_RELAY,
)

_LOGGER = logging.getLogger(__name__)


def _match_area(name: str, areas: dict[str, str]) -> str | None:
    """Match a group tag name to an existing HA area.

    Checks if any existing area name appears within the tag name.
    Prefers longer (more specific) matches.
    """
    name_lower = name.lower()
    for area_lower, area_name in sorted(
        areas.items(), key=lambda x: len(x[0]), reverse=True
    ):
        if area_lower in name_lower:
            return area_name
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CGateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up C-Gate lights from a config entry."""
    client: CGateClient = entry.runtime_data

    # Build area lookup for matching tag names to rooms
    area_reg = ar.async_get(hass)
    areas: dict[str, str] = {
        area.name.lower(): area.name
        for area in area_reg.async_list_areas()
    }

    # Get group type overrides from options
    group_overrides: dict[str, str] = entry.options.get(
        CONF_GROUP_OVERRIDES, {}
    )

    # Track which groups already have entities
    known_groups: set[str] = set()

    # Only handle groups assigned to the light platform (dimmer or relay)
    light_types = {GROUP_TYPE_DIMMER, GROUP_TYPE_RELAY}

    # Discover existing groups from C-Gate via TREE + GET TagName
    discovered = await client.discover_lighting_groups()
    entities = []
    for group in discovered:
        group_type = group_overrides.get(group.unique_id, DEFAULT_GROUP_TYPE)
        if group_type not in light_types:
            continue
        known_groups.add(group.unique_id)
        suggested_area = _match_area(group.name, areas) if group.name else None
        is_relay = group_type == GROUP_TYPE_RELAY
        entities.append(
            CGateLight(client, group, entry.entry_id, suggested_area, is_relay)
        )
    if entities:
        _LOGGER.info("Discovered %d lighting groups from C-Gate", len(entities))
        async_add_entities(entities)

    # Also handle dynamically discovered groups from SCP events
    @callback
    def _handle_status_update(group: CGateGroup) -> None:
        """Handle a new or updated group from SCP."""
        if group.unique_id not in known_groups:
            group_type = group_overrides.get(
                group.unique_id, DEFAULT_GROUP_TYPE
            )
            if group_type not in light_types:
                return
            known_groups.add(group.unique_id)
            suggested_area = (
                _match_area(group.name, areas) if group.name else None
            )
            is_relay = group_type == GROUP_TYPE_RELAY
            async_add_entities(
                [CGateLight(client, group, entry.entry_id, suggested_area, is_relay)]
            )

    unsub = client.register_status_callback(_handle_status_update)
    entry.async_on_unload(unsub)


class CGateLight(LightEntity):
    """Representation of a C-Bus light controlled via C-Gate."""

    _attr_has_entity_name = False

    def __init__(
        self,
        client: CGateClient,
        group: CGateGroup,
        entry_id: str,
        suggested_area: str | None = None,
        is_relay: bool = False,
    ) -> None:
        """Initialize the light."""
        self._client = client
        self._group = group
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{group.unique_id}"
        self._attr_name = group.name or f"Group {group.group}"
        self._suggested_area = suggested_area
        self._is_relay = is_relay
        self.entity_id = f"light.sl_group_{group.group}"

        if is_relay:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}
        else:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to group this entity under a device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._group.unique_id}")},
            name=self._attr_name,
            manufacturer="Schneider Electric",
            model="C-Bus Lighting Group",
            via_device=(DOMAIN, self._entry_id),
            suggested_area=self._suggested_area,
        )

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self._group.level > 0

    @property
    def brightness(self) -> int | None:
        """Return the brightness (0-255, native C-Bus range)."""
        if self._is_relay:
            return None
        return self._group.level if self.is_on else None

    @property
    def available(self) -> bool:
        """Return True if the C-Gate connection is active."""
        return self._client.connected

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        if self._is_relay:
            await self._client.turn_on(self._group)
        else:
            brightness = kwargs.get(ATTR_BRIGHTNESS, CBUS_LEVEL_MAX)
            transition = kwargs.get(ATTR_TRANSITION)

            if transition is not None:
                await self._client.ramp(
                    self._group, brightness, transition=int(transition)
                )
            elif brightness == CBUS_LEVEL_MAX:
                await self._client.turn_on(self._group)
            else:
                await self._client.ramp(self._group, brightness)

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        transition = kwargs.get(ATTR_TRANSITION)
        if transition is not None:
            await self._client.ramp(
                self._group, 0, transition=int(transition)
            )
        else:
            await self._client.turn_off(self._group)
        self.async_write_ha_state()

    @callback
    def _handle_group_update(self, group: CGateGroup) -> None:
        """Handle a status update for this group."""
        if group.unique_id == self._group.unique_id:
            self.async_write_ha_state()

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        """Publish availability immediately when the connection changes."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for status updates when entity is added."""
        self.async_on_remove(
            self._client.register_status_callback(self._handle_group_update)
        )
        self.async_on_remove(
            self._client.register_connection_callback(self._handle_connection_change)
        )

    async def async_update(self) -> None:
        """Fetch the latest level from C-Gate."""
        if self._client.connected:
            await self._client.get_level(self._group)
