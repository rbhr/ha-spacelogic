"""The SpaceLogic C-Gate integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .cgate import CGateClient, CGateCommandError, CGateConnectionError
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

    entry.async_on_unload(client.disconnect)
    try:
        try:
            await client.connect()
        except (CGateConnectionError, CGateCommandError) as err:
            # Must be ConfigEntryNotReady, not a bare raise: without it HA treats
            # setup as permanently failed and never retries, so a C-Gate that is
            # merely slow to come up needs a manual reload. That is exactly what
            # happened on 2026-08-26.
            raise ConfigEntryNotReady(
                f"Cannot connect to C-Gate at {client.host}:{client.command_port}"
            ) from err

        entry.runtime_data = client

        # Seed measurement readings before the platforms load. Sensors are otherwise
        # created only reactively from broadcasts, so after a restart they sit
        # unknown until each channel happens to report on its own schedule.
        await _async_seed_measurements(hass, entry, client)

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    except BaseException:
        await client.disconnect()
        raise

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    return True


async def _async_seed_measurements(
    hass: HomeAssistant, entry: CGateConfigEntry, client: CGateClient
) -> None:
    """Poll every known measurement channel once, before platforms set up."""
    from .sensor import _known_channels  # noqa: PLC0415 - avoids a circular import

    channels = _known_channels(hass, entry)
    if not channels:
        # First run: nothing in the registry yet, so probe for channels.
        try:
            channels = await client.scan_measurement_channels()
        except (CGateConnectionError, CGateCommandError):
            _LOGGER.debug("Measurement channel scan failed; relying on broadcasts")
            return

    try:
        found = await client.async_refresh_measurements(channels)
    except (CGateConnectionError, CGateCommandError):
        _LOGGER.debug("Measurement seeding failed; relying on broadcasts")
        return
    _LOGGER.debug("Seeded %d of %d measurement channels", found, len(channels))


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
