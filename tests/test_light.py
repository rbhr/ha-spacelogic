"""Tests for the CGateLight entity."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from custom_components.spacelogic_cgate.cgate import CGateClient, CGateGroup
from custom_components.spacelogic_cgate.light import CGateLight


def _setattr(obj: object, name: str, value: object) -> None:
    """Set attribute bypassing MagicMock __setattr__.

    HA exposes ``_attr_*`` names as cached-property data descriptors, which take
    precedence over the instance ``__dict__``; those must go through the
    descriptor or the write is silently ignored on read-back.
    """
    for klass in type(obj).__mro__:
        descriptor = klass.__dict__.get(name)
        if descriptor is not None:
            if hasattr(descriptor, "__set__"):
                descriptor.__set__(obj, value)
                return
            break
    object.__getattribute__(obj, "__dict__")[name] = value


def _make_light(
    client: CGateClient,
    group_num: int = 1,
    level: int = 0,
    name: str = "Test Light",
    is_relay: bool = False,
) -> CGateLight:
    group = CGateGroup(
        network=254, application=56, group=group_num, level=level, name=name
    )
    client._groups[group.unique_id] = group
    light = object.__new__(CGateLight)
    # Bypass MagicMock __setattr__ by writing directly to __dict__
    _setattr(light, "_client", client)
    _setattr(light, "_group", group)
    _setattr(light, "_entry_id", "test_entry")
    _setattr(light, "_attr_unique_id", f"test_entry_{group.unique_id}")
    _setattr(light, "_attr_name", name)
    _setattr(light, "_attr_has_entity_name", False)
    _setattr(light, "_suggested_area", None)
    _setattr(light, "_is_relay", is_relay)
    _setattr(light, "entity_id", f"light.sl_group_{group.group}")
    # Stub async_write_ha_state since we don't have HA runtime
    _setattr(light, "async_write_ha_state", lambda: None)
    if is_relay:
        _setattr(light, "_attr_color_mode", "onoff")
        _setattr(light, "_attr_supported_color_modes", {"onoff"})
    else:
        _setattr(light, "_attr_color_mode", "brightness")
        _setattr(light, "_attr_supported_color_modes", {"brightness"})
    return light


class TestCGateLightDimmer:
    """Tests for a dimmable (default) light."""

    def test_default_is_dimmer(self, mock_cgate_client: CGateClient) -> None:
        light = _make_light(mock_cgate_client)
        assert light._attr_color_mode == "brightness"
        assert light._attr_supported_color_modes == {"brightness"}

    def test_brightness_when_on(self, mock_cgate_client: CGateClient) -> None:
        light = _make_light(mock_cgate_client, level=128)
        assert light.brightness == 128
        assert light.is_on is True

    def test_brightness_when_off(self, mock_cgate_client: CGateClient) -> None:
        light = _make_light(mock_cgate_client, level=0)
        assert light.brightness is None
        assert light.is_on is False

    def test_turn_on_with_brightness(
        self, mock_cgate_client: CGateClient
    ) -> None:
        light = _make_light(mock_cgate_client)
        mock_cgate_client.ramp = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            light.async_turn_on(brightness=128)
        )
        mock_cgate_client.ramp.assert_called_once_with(light._group, 128)

    def test_turn_on_full_brightness(
        self, mock_cgate_client: CGateClient
    ) -> None:
        light = _make_light(mock_cgate_client)
        mock_cgate_client.turn_on = AsyncMock()
        asyncio.get_event_loop().run_until_complete(light.async_turn_on())
        mock_cgate_client.turn_on.assert_called_once_with(light._group)

    def test_turn_on_with_transition(
        self, mock_cgate_client: CGateClient
    ) -> None:
        light = _make_light(mock_cgate_client)
        mock_cgate_client.ramp = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            light.async_turn_on(brightness=200, transition=5)
        )
        mock_cgate_client.ramp.assert_called_once_with(
            light._group, 200, transition=5
        )


class TestCGateLightRelay:
    """Tests for a relay (on/off only) light."""

    def test_relay_color_mode(self, mock_cgate_client: CGateClient) -> None:
        light = _make_light(mock_cgate_client, is_relay=True)
        assert light._attr_color_mode == "onoff"
        assert light._attr_supported_color_modes == {"onoff"}

    def test_relay_no_brightness(self, mock_cgate_client: CGateClient) -> None:
        light = _make_light(mock_cgate_client, level=255, is_relay=True)
        assert light.brightness is None
        assert light.is_on is True

    def test_relay_off(self, mock_cgate_client: CGateClient) -> None:
        light = _make_light(mock_cgate_client, level=0, is_relay=True)
        assert light.is_on is False

    def test_relay_turn_on_ignores_brightness(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Relay lights should always use turn_on, never ramp."""
        light = _make_light(mock_cgate_client, is_relay=True)
        mock_cgate_client.turn_on = AsyncMock()
        mock_cgate_client.ramp = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            light.async_turn_on(brightness=128)
        )
        mock_cgate_client.turn_on.assert_called_once_with(light._group)
        mock_cgate_client.ramp.assert_not_called()

    def test_relay_turn_off(
        self, mock_cgate_client: CGateClient
    ) -> None:
        light = _make_light(mock_cgate_client, level=255, is_relay=True)
        mock_cgate_client.turn_off = AsyncMock()
        asyncio.get_event_loop().run_until_complete(light.async_turn_off())
        mock_cgate_client.turn_off.assert_called_once_with(light._group)
