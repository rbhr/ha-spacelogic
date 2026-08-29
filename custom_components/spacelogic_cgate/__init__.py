"""The SpaceLogic C-Gate integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .cgate import CGateClient, CGateConnectionError
from .const import (
    CONF_COMMAND_PORT,
    CONF_EVENT_PORT,
    CONF_PROJECT_NAME,
    CONF_STATUS_CHANGE_PORT,
    DEFAULT_COMMAND_PORT,
    DEFAULT_EVENT_PORT,
    DEFAULT_STATUS_CHANGE_PORT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]

CGateConfigEntry = ConfigEntry[CGateClient]


async def async_setup_entry(hass: HomeAssistant, entry: CGateConfigEntry) -> bool:
    """Set up SpaceLogic C-Gate from a config entry."""
    client = CGateClient(
        host=entry.data[CONF_HOST],
        command_port=entry.data.get(CONF_COMMAND_PORT, DEFAULT_COMMAND_PORT),
        event_port=entry.data.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT),
        status_change_port=entry.data.get(
            CONF_STATUS_CHANGE_PORT, DEFAULT_STATUS_CHANGE_PORT
        ),
        project_name=entry.data[CONF_PROJECT_NAME],
    )

    # Raise rather than return False so Home Assistant retries the entry with
    # its own backoff. A C-Gate that is down when HA boots — the two often
    # restart together — otherwise leaves the integration dead until someone
    # reloads it by hand. Once this first attempt succeeds the client supervises
    # its own connection and later outages never come back through here.
    try:
        await client.connect()
    except CGateConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: CGateConfigEntry
) -> None:
    """Reload the integration when options or connection details change.

    HA only fires this when the entry actually changed, which makes it the
    single reload path for both the options flow and the reconfigure flow.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CGateConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.disconnect()
    return unload_ok
