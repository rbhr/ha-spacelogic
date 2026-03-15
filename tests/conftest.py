"""Fixtures for SpaceLogic C-Gate tests."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Mock homeassistant modules so we can import cgate.py without HA installed
ha_mock = MagicMock()
# Make @callback a passthrough so decorated methods remain callable in tests
ha_mock.callback = lambda fn: fn
sys.modules.setdefault("homeassistant", ha_mock)
sys.modules.setdefault("homeassistant.config_entries", ha_mock)
sys.modules.setdefault("homeassistant.const", ha_mock)
sys.modules.setdefault("homeassistant.core", ha_mock)
sys.modules.setdefault("homeassistant.components", ha_mock)
sys.modules.setdefault("homeassistant.helpers", ha_mock)
sys.modules.setdefault("homeassistant.helpers.area_registry", ha_mock)
sys.modules.setdefault("homeassistant.helpers.device_registry", ha_mock)
sys.modules.setdefault("homeassistant.helpers.entity_platform", ha_mock)
sys.modules.setdefault("homeassistant.helpers.selector", MagicMock())
sys.modules.setdefault("voluptuous", MagicMock())

# Create a proper mock for light component with real ColorMode values
light_mock = MagicMock()


class _ColorMode:
    ONOFF = "onoff"
    BRIGHTNESS = "brightness"


light_mock.ColorMode = _ColorMode
light_mock.ATTR_BRIGHTNESS = "brightness"
light_mock.ATTR_TRANSITION = "transition"
light_mock.LightEntity = MagicMock
sys.modules.setdefault("homeassistant.components.light", light_mock)

# Mock other HA component modules used by new platforms
for mod_name in (
    "homeassistant.components.switch",
    "homeassistant.components.cover",
    "homeassistant.components.lock",
    "homeassistant.components.fan",
    "homeassistant.components.valve",
    "homeassistant.components.sensor",
):
    mock = MagicMock()
    # Provide MagicMock as base class so entity classes can inherit
    for attr in (
        "SwitchEntity", "CoverEntity", "LockEntity", "FanEntity",
        "ValveEntity", "SensorEntity",
    ):
        setattr(mock, attr, MagicMock)
    sys.modules.setdefault(mod_name, mock)

from custom_components.spacelogic_cgate.cgate import CGateClient  # noqa: E402
from custom_components.spacelogic_cgate.const import (  # noqa: E402
    DEFAULT_COMMAND_PORT,
    DEFAULT_EVENT_PORT,
    DEFAULT_STATUS_CHANGE_PORT,
)


@pytest.fixture
def mock_cgate_client() -> CGateClient:
    """Create a mock CGateClient."""
    client = CGateClient(
        host="192.168.1.100",
        command_port=DEFAULT_COMMAND_PORT,
        event_port=DEFAULT_EVENT_PORT,
        status_change_port=DEFAULT_STATUS_CHANGE_PORT,
        project_name="TEST_PROJECT",
    )
    client.connect = AsyncMock()  # type: ignore[method-assign]
    client.disconnect = AsyncMock()  # type: ignore[method-assign]
    client._connected = True
    return client


@pytest.fixture
def mock_config_data() -> dict:
    """Return mock config entry data."""
    return {
        "host": "192.168.1.100",
        "command_port": DEFAULT_COMMAND_PORT,
        "event_port": DEFAULT_EVENT_PORT,
        "status_change_port": DEFAULT_STATUS_CHANGE_PORT,
        "project_name": "TEST_PROJECT",
    }
