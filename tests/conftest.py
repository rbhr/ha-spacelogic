"""Fixtures for SpaceLogic C-Gate tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.spacelogic_cgate.cgate import CGateClient
from custom_components.spacelogic_cgate.const import (
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
