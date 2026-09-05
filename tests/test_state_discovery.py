"""Regression tests for isolation, unknown state, discovery and HA features."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

import custom_components
from custom_components import spacelogic_cgate as integration
from custom_components.spacelogic_cgate import cgate, sensor
from custom_components.spacelogic_cgate.cgate import CGateClient, CGateCommandError
from custom_components.spacelogic_cgate.const import CONF_GROUP_OVERRIDES, DOMAIN

from .test_lifecycle import CUSTOM_COMPONENTS_PATH

GROUP_TYPES = ["light_dimmer", "light_relay", "cover", "fan", "lock", "switch", "valve"]
ENTITY_DOMAINS = ["light", "light", "cover", "fan", "lock", "switch", "valve"]
GROUP_XML = "".join(
    f'<Group Address="{number}" TagName="Device {number}"/>' for number in range(1, 8)
)
PROJECT_XML = (
    '<Installation><Project Address="HOME"><Network Address="1">'
    f'<Application Address="56">{GROUP_XML}</Application></Network>'
    '<Network Address="2"><Application Address="228"/></Network></Project>'
    # Another project with identical group addresses must not overwrite ours.
    '<Project Address="OTHER"><Network Address="1"><Application Address="56">'
    '<Group Address="1" TagName="Wrong project"/></Application></Network>'
    '<Network Address="99"/></Project></Installation>'
)


@pytest.fixture
async def loaded(hass, enable_custom_integrations, monkeypatch):
    """Load all platforms through real HA, simulating only the wire exchange."""
    client = CGateClient("test", 1, 2, 3, "HOME")
    client._connected = True
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "test", "project_name": "HOME"},
        options={CONF_GROUP_OVERRIDES: {
            f"1_56_{number}": kind for number, kind in enumerate(GROUP_TYPES, 1)
        }},
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name="C-Gate"
    )
    commands = []
    readings = {"//HOME/2/228/0/0": 100}

    async def exchange(command, **kwargs):
        commands.append(command)
        if command.startswith("DBGETXML"):
            return ["343-Begin XML", f"347-{PROJECT_XML}", "344 End XML"]
        if command.endswith(" level"):
            group = client.groups[command.split()[1].replace("/", "_")]
            if group.level is None:
                raise CGateCommandError("network syncing", code=408)
            return [f"300 {group.address}: level={group.level}"]
        if command.endswith(" Data"):
            address = command.split()[1]
            if address not in readings:
                raise CGateCommandError("missing channel", code=401)
            return [f"300 {address}: Data={readings[address]},0,38,0"]
        return ["200 OK"]

    # Keep the probe small while exercising discovery on multiple networks.
    monkeypatch.setattr(cgate, "MEASUREMENT_SCAN_MAX_DEVICE", 0)
    monkeypatch.setattr(cgate, "MEASUREMENT_SCAN_MAX_CHANNEL", 0)
    with (
        patch.object(custom_components, "__path__", [CUSTOM_COMPONENTS_PATH]),
        patch.object(integration, "CGateClient", return_value=client),
        patch.object(client, "connect", AsyncMock()),
        patch.object(client, "_send_receive", AsyncMock(side_effect=exchange)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        try:
            yield client, entry, commands, readings
        finally:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


async def test_startup_discovers_once_and_probes_all_project_networks(loaded):
    client, _, commands, _ = loaded
    assert commands.count("DBGETXML //HOME") == 1
    assert [cmd for cmd in commands if cmd.endswith(" level")] == [
        f"GET 1/56/{number} level" for number in range(1, 8)
    ]
    assert [cmd for cmd in commands if cmd.endswith(" Data")] == [
        "GET //HOME/1/228/0/0 Data", "GET //HOME/2/228/0/0 Data"
    ]
    assert commands.index("GET 1/56/7 level") < commands.index("GET //HOME/1/228/0/0 Data")
    assert client.groups["1_56_1"].name == "Device 1"
    assert client.measurements["2_228_0_0"].value == 100


async def test_unread_entities_stay_unknown_until_an_event(hass, loaded):
    client, _, _, _ = loaded
    for number, domain in enumerate(ENTITY_DOMAINS, 1):
        assert hass.states.get(f"{domain}.sl_group_{number}").state == "unknown"
    assert "current_position" not in hass.states.get("cover.sl_group_3").attributes
    assert hass.states.get("fan.sl_group_4").attributes.get("percentage") is None
    client._handle_scp_event("lighting off //HOME/1/56/5 #sourceunit=1")
    assert hass.states.get("lock.sl_group_5").state == "locked"
    client._handle_scp_event("lighting on //HOME/1/56/5 #sourceunit=1")
    assert hass.states.get("lock.sl_group_5").state == "unlocked"


async def test_light_transitions_reach_cgate_and_relay_stays_on_off(hass, loaded):
    _, _, commands, _ = loaded
    await hass.services.async_call("light", "turn_on", {
        "entity_id": "light.sl_group_1", "brightness": 100, "transition": 10,
    }, blocking=True)
    assert "RAMP 1/56/1 100 10s" in commands
    await hass.services.async_call("light", "turn_off", {
        "entity_id": "light.sl_group_1", "transition": 5,
    }, blocking=True)
    assert "RAMP 1/56/1 0 5s" in commands
    await hass.services.async_call("light", "turn_off", {
        "entity_id": "light.sl_group_2", "transition": 5,
    }, blocking=True)
    assert "OFF 1/56/2" in commands


async def test_fan_on_off_services_reach_cgate(hass, loaded):
    _, _, commands, _ = loaded
    await hass.services.async_call("fan", "turn_on", {"entity_id": "fan.sl_group_4"},
                                   blocking=True)
    assert "ON 1/56/4" in commands
    assert hass.states.get("fan.sl_group_4").state == "on"
    await hass.services.async_call("fan", "turn_off", {"entity_id": "fan.sl_group_4"},
                                   blocking=True)
    assert "OFF 1/56/4" in commands
    assert hass.states.get("fan.sl_group_4").state == "off"


async def test_new_measurement_joins_next_poll_and_reconnect_refresh(hass, loaded):
    client, _, commands, readings = loaded
    address = "//HOME/2/228/4/5"
    client._handle_scp_event(f"measurement data {address} 123 0 38 #sourceunit=1")
    readings[address] = 456
    await hass.async_block_till_done()
    commands.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert commands.count(f"GET {address} Data") == 1
    assert float(hass.states.get("sensor.sl_meas_4_5").state) == 456

    readings[address] = 789
    commands.clear()
    client._connected = False
    client._notify_connection(False)
    client._connected = True
    client._notify_connection(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert commands.count(f"GET {address} Data") == 1
    assert float(hass.states.get("sensor.sl_meas_4_5").state) == 789


async def test_live_channels_are_known_before_registry_creation(hass, loaded):
    client, entry, _, _ = loaded
    # Directly stamp a valid reading to test the gap before entity registration.
    client._update_measurement(network=2, application=228, device=4, channel=5,
                               raw_value=123, exponent=0, units=38)
    assert (2, 4, 5) in sensor._known_channels(hass, entry)
    channels = sensor._known_channels(hass, entry)
    assert len(channels) == len(set(channels))


async def test_overlapping_measurement_refreshes_are_coalesced(hass, loaded):
    client, _, _, _ = loaded
    started, release = asyncio.Event(), asyncio.Event()

    async def refresh(channels):
        started.set()
        await release.wait()
        return len(channels)

    with patch.object(client, "async_refresh_measurements", AsyncMock(side_effect=refresh)) as poll:
        client._notify_connection(True)
        await asyncio.wait_for(started.wait(), 1)
        try:
            client._notify_connection(True)
            await hass.async_block_till_done()
            assert poll.await_count == 1
        finally:
            release.set()
            await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize("event", [
    "lighting on //OTHER/254/56/1 #sourceunit=1",
    "lighting on //TEST_PROJECT/254/57/1 #sourceunit=1",
    "measurement data //OTHER/254/228/1/1 999 0 38 #sourceunit=1",
    "measurement data //TEST_PROJECT/254/56/1/1 999 0 38 #sourceunit=1",
])
def test_foreign_events_do_not_create_or_update_objects(mock_cgate_client, event):
    group = mock_cgate_client._get_or_create_group(254, 56, 1)
    group.level = 100
    measurement = mock_cgate_client._update_measurement(
        network=254, application=228, device=1, channel=1, raw_value=100,
        exponent=0, units=38,
    )
    seen = measurement.last_seen
    updates = []
    mock_cgate_client.register_status_callback(updates.append)
    mock_cgate_client.register_measurement_callback(updates.append)
    mock_cgate_client._handle_scp_event(event)
    assert group.level == 100
    assert measurement.raw_value == 100
    assert measurement.last_seen == seen
    assert len(mock_cgate_client.groups) == len(mock_cgate_client.measurements) == 1
    assert not updates


@pytest.mark.parametrize("address", [
    "//OTHER/254/228/1/1", "//TEST_PROJECT/254/56/1/1",
    "//TEST_PROJECT/1/228/1/1", "//TEST_PROJECT/254/228/2/1",
    "//TEST_PROJECT/254/228/1/2",
])
async def test_polled_measurements_require_complete_address(mock_cgate_client, address):
    with patch.object(mock_cgate_client, "_send_command", AsyncMock(
        return_value=f"300 {address}: Data=999,0,38,0"
    )):
        assert not await mock_cgate_client.read_measurement(254, 1, 1)
    assert not mock_cgate_client.measurements


async def test_virtual_group_retains_commands_and_events(mock_cgate_client):
    group = mock_cgate_client._get_or_create_group(254, 56, 1)
    with patch.object(mock_cgate_client, "_send_command", AsyncMock(
        side_effect=CGateCommandError("no object", code=401)
    )):
        assert await mock_cgate_client.get_level(group) is None
    assert group.is_virtual
    with patch.object(mock_cgate_client, "_send_command", AsyncMock(return_value="200 OK")) as send:
        await mock_cgate_client.turn_on(group)
        assert await mock_cgate_client.get_level(group) == 255
        mock_cgate_client._handle_scp_event(
            "lighting ramp //TEST_PROJECT/254/56/1 128 #sourceunit=1"
        )
        assert await mock_cgate_client.get_level(group) == 128
        await mock_cgate_client.turn_off(group)
        assert await mock_cgate_client.get_level(group) == 0
        assert send.await_count == 2


@pytest.mark.parametrize("reply", [[], ["347-<broken"], ["401 missing"]])
async def test_failed_discovery_retries_setup(hass, reply):
    client = CGateClient("test", 1, 2, 3, "HOME")
    client._connected = True
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "test", "project_name": "HOME"})
    entry.add_to_hass(hass)
    with (
        patch.object(integration, "CGateClient", return_value=client),
        patch.object(client, "connect", AsyncMock()),
        patch.object(client, "_send_receive", AsyncMock(return_value=reply)),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()) as forward,
    ):
        with pytest.raises(ConfigEntryNotReady):
            await integration.async_setup_entry(hass, entry)
        forward.assert_not_called()
        assert not client.connected
        await entry._async_process_on_unload(hass)
