"""Recovery integration tests using real Home Assistant entries and entities."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components
from custom_components import spacelogic_cgate as integration
from custom_components.spacelogic_cgate.cgate import (
    CGateClient,
    CGateConnectionError,
    CGateGroup,
)
from custom_components.spacelogic_cgate.const import CONF_GROUP_OVERRIDES, DOMAIN

CUSTOM_COMPONENTS_PATH = str(Path(__file__).resolve().parents[1] / "custom_components")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_real_entities_publish_outages_resync_and_unsubscribe(hass, mock_config_data):
    client = CGateClient("test", 1, 2, 3, "HOME")
    client._connected = True
    types = ["light_dimmer", "cover", "fan", "lock", "switch", "valve"]
    domains = ["light", *types[1:]]
    for number in range(1, 7):
        group = CGateGroup(254, 56, number, level=100, name=f"Group {number}")
        client.groups[group.unique_id] = group
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=mock_config_data,
        options={CONF_GROUP_OVERRIDES: {
            group.unique_id: kind for group, kind in zip(client.groups.values(), types, strict=True)
        }},
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name="C-Gate"
    )
    # A previously registered channel must keep polling after reconnect.
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_meas_254_228_1_1", config_entry=entry
    )

    async def reply(command):
        if command.endswith(" Data"):
            return "300 //HOME/254/228/1/1: Data=234,-1,0,0"
        return f"300 {command.split()[1]}: level=42"

    with (
        # Editable namespace installs add a synthetic finder path that HA's
        # filesystem discovery cannot scan. Use the real integration directory.
        patch.object(custom_components, "__path__", [CUSTOM_COMPONENTS_PATH]),
        patch.object(integration, "CGateClient", return_value=client),
        patch.object(client, "connect", AsyncMock()),
        patch.object(client, "discover_lighting_groups", AsyncMock(
            return_value=list(client.groups.values())
        )) as discover,
        patch.object(client, "_send_command", AsyncMock(side_effect=reply)),
        patch.object(client, "async_refresh_measurements",
                     wraps=client.async_refresh_measurements) as refresh,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        discover.assert_awaited_once()
        entity_ids = [f"{domain}.sl_group_{number}" for number, domain in enumerate(domains, 1)]
        for entity_id in entity_ids:
            assert hass.states.get(entity_id) is not None
            assert hass.states.get(entity_id).state != "unavailable"

        client._mark_disconnected("test outage")
        assert all(hass.states.get(uid).state == "unavailable" for uid in entity_ids)
        before = refresh.await_count
        client._connected = True
        client._notify_connection(True)
        await client._resync_state()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert all(hass.states.get(uid).state != "unavailable" for uid in entity_ids)
        assert hass.states.get(entity_ids[0]).attributes["brightness"] == 42
        assert refresh.await_count > before
        assert client.measurements["254_228_1_1"].value == 23.4

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert client._connection_callbacks == []
        assert client._status_callbacks == []
        assert client._measurement_callbacks == []
        assert not client.connected


@pytest.mark.parametrize("stage", ["seed", "platforms"])
@pytest.mark.parametrize("error", [RuntimeError("setup failed"), asyncio.CancelledError()])
async def test_interrupted_entry_setup_disconnects(hass, mock_config_data, stage, error):
    client = CGateClient("test", 1, 2, 3, "HOME")
    entry = MockConfigEntry(domain=DOMAIN, data=mock_config_data)
    entry.add_to_hass(hass)
    with (
        patch.object(integration, "CGateClient", return_value=client),
        patch.object(client, "connect", AsyncMock()),
        patch.object(client, "disconnect", wraps=client.disconnect) as disconnect,
        patch.object(client, "discover_lighting_groups", AsyncMock(return_value=[])),
        patch.object(integration, "_async_seed_measurements", AsyncMock(
            side_effect=error if stage == "seed" else None
        )),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock(
            side_effect=error if stage == "platforms" else None
        )),
    ):
        with pytest.raises(type(error)):
            await integration.async_setup_entry(hass, entry)
        disconnect.assert_awaited_once()
        # HA may subsequently invoke the registered cleanup too.
        await entry._async_process_on_unload(hass)
        assert disconnect.await_count == 2
        assert client._supervisor_task is None


async def test_unreachable_at_boot_requests_ha_retry(hass, mock_config_data):
    client = CGateClient("test", 1, 2, 3, "HOME")
    entry = MockConfigEntry(domain=DOMAIN, data=mock_config_data)
    entry.add_to_hass(hass)
    with (
        patch.object(integration, "CGateClient", return_value=client),
        patch.object(client, "connect", AsyncMock(side_effect=CGateConnectionError("down"))),
        patch.object(client, "disconnect", wraps=client.disconnect) as disconnect,
        patch.object(client, "discover_lighting_groups", AsyncMock(return_value=[])),
    ):
        with pytest.raises(ConfigEntryNotReady):
            await integration.async_setup_entry(hass, entry)
        disconnect.assert_awaited_once()
        await entry._async_process_on_unload(hass)
