"""Tests for switch, cover, lock, fan, valve, and sensor entities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from custom_components.spacelogic_cgate.cgate import (
    CGateClient,
    CGateGroup,
    CGateMeasurement,
)
from custom_components.spacelogic_cgate.cover import CGateCover
from custom_components.spacelogic_cgate.fan import CGateFan
from custom_components.spacelogic_cgate.lock import CGateLock
from custom_components.spacelogic_cgate.sensor import CGateMeasurementSensor
from custom_components.spacelogic_cgate.switch import CGateSwitch
from custom_components.spacelogic_cgate.valve import CGateValve


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


def _make_entity(
    cls: type,
    client: CGateClient,
    group_num: int = 1,
    level: int = 0,
    name: str = "Test Group",
    **extra_attrs: object,
) -> object:
    """Create an entity instance bypassing MagicMock base class issues."""
    group = CGateGroup(
        network=254, application=56, group=group_num, level=level, name=name
    )
    client._groups[group.unique_id] = group
    entity = object.__new__(cls)
    _setattr(entity, "_client", client)
    _setattr(entity, "_group", group)
    _setattr(entity, "_entry_id", "test_entry")
    _setattr(entity, "_attr_unique_id", f"test_entry_{group.unique_id}")
    _setattr(entity, "_attr_name", name)
    _setattr(entity, "_attr_has_entity_name", False)
    _setattr(entity, "_suggested_area", None)
    _setattr(entity, "entity_id", f"entity.sl_group_{group.group}")
    _setattr(entity, "async_write_ha_state", lambda: None)
    for k, v in extra_attrs.items():
        _setattr(entity, k, v)
    return entity


def _make_sensor(
    client: CGateClient,
    device: int = 1,
    channel: int = 1,
    units: int = 0x26,  # Watts
    raw_value: int = 11104,
    exponent: int = -1,
) -> CGateMeasurementSensor:
    """Create a measurement sensor entity."""
    meas = CGateMeasurement(
        network=254, application=228, device=device, channel=channel,
        raw_value=raw_value, exponent=exponent, units=units,
    )
    client._measurements[meas.unique_id] = meas
    entity = object.__new__(CGateMeasurementSensor)
    # Initialize mock internals so __setattr__/__getattr__ work properly
    _setattr(entity, "_mock_methods", None)
    _setattr(entity, "_mock_unsafe", False)
    _setattr(entity, "_spec_set", None)
    _setattr(entity, "_mock_sealed", False)
    _setattr(entity, "_mock_children", {})
    _setattr(entity, "_client", client)
    _setattr(entity, "_measurement", meas)
    _setattr(entity, "_entry_id", "test_entry")
    _setattr(entity, "_attr_unique_id", f"test_entry_meas_{meas.unique_id}")
    _setattr(entity, "_attr_name", f"CH{channel} Power")
    _setattr(entity, "_attr_has_entity_name", False)
    _setattr(entity, "async_write_ha_state", lambda: None)
    return entity


# ── Switch Tests ──


class TestCGateSwitch:
    def test_is_on(self, mock_cgate_client: CGateClient) -> None:
        sw = _make_entity(CGateSwitch, mock_cgate_client, level=255)
        assert sw.is_on is True

    def test_is_off(self, mock_cgate_client: CGateClient) -> None:
        sw = _make_entity(CGateSwitch, mock_cgate_client, level=0)
        assert sw.is_on is False

    def test_turn_on(self, mock_cgate_client: CGateClient) -> None:
        sw = _make_entity(CGateSwitch, mock_cgate_client)
        mock_cgate_client.turn_on = AsyncMock()
        asyncio.get_event_loop().run_until_complete(sw.async_turn_on())
        mock_cgate_client.turn_on.assert_called_once_with(sw._group)

    def test_turn_off(self, mock_cgate_client: CGateClient) -> None:
        sw = _make_entity(CGateSwitch, mock_cgate_client, level=255)
        mock_cgate_client.turn_off = AsyncMock()
        asyncio.get_event_loop().run_until_complete(sw.async_turn_off())
        mock_cgate_client.turn_off.assert_called_once_with(sw._group)


# ── Cover Tests ──


class TestCGateCover:
    def test_is_closed(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client, level=0)
        assert cover.is_closed is True

    def test_is_open(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client, level=255)
        assert cover.is_closed is False

    def test_position_fully_open(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client, level=255)
        assert cover.current_cover_position == 100

    def test_position_half(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client, level=128)
        assert cover.current_cover_position == 50

    def test_position_closed(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client, level=0)
        assert cover.current_cover_position == 0

    def test_open_cover(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client)
        mock_cgate_client.turn_on = AsyncMock()
        asyncio.get_event_loop().run_until_complete(cover.async_open_cover())
        mock_cgate_client.turn_on.assert_called_once_with(cover._group)

    def test_close_cover(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client, level=255)
        mock_cgate_client.turn_off = AsyncMock()
        asyncio.get_event_loop().run_until_complete(cover.async_close_cover())
        mock_cgate_client.turn_off.assert_called_once_with(cover._group)

    def test_set_position(self, mock_cgate_client: CGateClient) -> None:
        cover = _make_entity(CGateCover, mock_cgate_client)
        mock_cgate_client.ramp = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            cover.async_set_cover_position(position=50)
        )
        # 50% of 255 = 128 (rounded)
        mock_cgate_client.ramp.assert_called_once_with(cover._group, 128)


# ── Lock Tests ──


class TestCGateLock:
    def test_is_locked(self, mock_cgate_client: CGateClient) -> None:
        lock = _make_entity(CGateLock, mock_cgate_client, level=0)
        assert lock.is_locked is True

    def test_is_unlocked(self, mock_cgate_client: CGateClient) -> None:
        lock = _make_entity(CGateLock, mock_cgate_client, level=255)
        assert lock.is_locked is False

    def test_lock(self, mock_cgate_client: CGateClient) -> None:
        lock = _make_entity(CGateLock, mock_cgate_client, level=255)
        mock_cgate_client.turn_off = AsyncMock()
        asyncio.get_event_loop().run_until_complete(lock.async_lock())
        mock_cgate_client.turn_off.assert_called_once_with(lock._group)

    def test_unlock(self, mock_cgate_client: CGateClient) -> None:
        lock = _make_entity(CGateLock, mock_cgate_client, level=0)
        mock_cgate_client.turn_on = AsyncMock()
        asyncio.get_event_loop().run_until_complete(lock.async_unlock())
        mock_cgate_client.turn_on.assert_called_once_with(lock._group)


# ── Fan Tests ──


class TestCGateFan:
    def test_is_on(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client, level=128)
        assert fan.is_on is True

    def test_is_off(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client, level=0)
        assert fan.is_on is False

    def test_percentage(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client, level=128)
        assert fan.percentage == 50

    def test_percentage_full(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client, level=255)
        assert fan.percentage == 100

    def test_percentage_off(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client, level=0)
        assert fan.percentage == 0

    def test_turn_on(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client)
        mock_cgate_client.turn_on = AsyncMock()
        asyncio.get_event_loop().run_until_complete(fan.async_turn_on())
        mock_cgate_client.turn_on.assert_called_once_with(fan._group)

    def test_turn_on_with_percentage(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client)
        mock_cgate_client.ramp = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            fan.async_turn_on(percentage=50)
        )
        # 50% of 255 = 128 (ceil)
        mock_cgate_client.ramp.assert_called_once_with(fan._group, 128)

    def test_turn_off(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client, level=255)
        mock_cgate_client.turn_off = AsyncMock()
        asyncio.get_event_loop().run_until_complete(fan.async_turn_off())
        mock_cgate_client.turn_off.assert_called_once_with(fan._group)

    def test_set_percentage(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client)
        mock_cgate_client.ramp = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            fan.async_set_percentage(75)
        )
        # 75% of 255 = 192 (ceil)
        mock_cgate_client.ramp.assert_called_once_with(fan._group, 192)

    def test_set_percentage_zero(self, mock_cgate_client: CGateClient) -> None:
        fan = _make_entity(CGateFan, mock_cgate_client, level=128)
        mock_cgate_client.turn_off = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            fan.async_set_percentage(0)
        )
        mock_cgate_client.turn_off.assert_called_once_with(fan._group)


# ── Valve Tests ──


class TestCGateValve:
    def test_is_closed(self, mock_cgate_client: CGateClient) -> None:
        valve = _make_entity(CGateValve, mock_cgate_client, level=0)
        assert valve.is_closed is True

    def test_is_open(self, mock_cgate_client: CGateClient) -> None:
        valve = _make_entity(CGateValve, mock_cgate_client, level=255)
        assert valve.is_closed is False

    def test_open_valve(self, mock_cgate_client: CGateClient) -> None:
        valve = _make_entity(CGateValve, mock_cgate_client)
        mock_cgate_client.turn_on = AsyncMock()
        asyncio.get_event_loop().run_until_complete(valve.async_open_valve())
        mock_cgate_client.turn_on.assert_called_once_with(valve._group)

    def test_close_valve(self, mock_cgate_client: CGateClient) -> None:
        valve = _make_entity(CGateValve, mock_cgate_client, level=255)
        mock_cgate_client.turn_off = AsyncMock()
        asyncio.get_event_loop().run_until_complete(valve.async_close_valve())
        mock_cgate_client.turn_off.assert_called_once_with(valve._group)


# ── Sensor Tests ──


class TestCGateMeasurementSensor:
    def test_native_value(self, mock_cgate_client: CGateClient) -> None:
        sensor = _make_sensor(mock_cgate_client, raw_value=18500, exponent=-1)
        assert sensor.native_value == 1850.0

    def test_native_value_small_exponent(self, mock_cgate_client: CGateClient) -> None:
        sensor = _make_sensor(
            mock_cgate_client, raw_value=3050, exponent=-2, units=0x00,
        )
        assert abs(sensor.native_value - 30.5) < 0.01

    def test_available(self, mock_cgate_client: CGateClient) -> None:
        sensor = _make_sensor(mock_cgate_client)
        assert sensor.available is True
        mock_cgate_client._connected = False
        assert sensor.available is False


class TestMeasurementSensorUpdate:
    """Tests for measurement sensor update handling."""

    def test_sensor_unique_id(self, mock_cgate_client: CGateClient) -> None:
        """Sensor unique_id is based on device and channel."""
        sensor = _make_sensor(
            mock_cgate_client, device=1, channel=1,
            raw_value=11104, exponent=-1,
        )
        assert sensor._attr_unique_id == "test_entry_meas_254_228_1_1"

    def test_sensor_update_matching_uid(self, mock_cgate_client: CGateClient) -> None:
        """Sensor accepts updates with matching unique_id."""
        sensor = _make_sensor(
            mock_cgate_client, device=1, channel=1,
            raw_value=11104, exponent=-1,
        )
        updated = []
        _setattr(sensor, "async_write_ha_state", lambda: updated.append(True))

        same_meas = CGateMeasurement(
            network=254, application=228, device=1, channel=1,
            raw_value=12000, exponent=-1,
        )
        sensor._handle_measurement_update(same_meas)
        assert len(updated) == 1

    def test_sensor_ignores_different_channel(self, mock_cgate_client: CGateClient) -> None:
        """Sensor ignores updates with different device/channel."""
        sensor = _make_sensor(
            mock_cgate_client, device=1, channel=1,
            raw_value=11104, exponent=-1,
        )
        updated = []
        _setattr(sensor, "async_write_ha_state", lambda: updated.append(True))

        other_meas = CGateMeasurement(
            network=254, application=228, device=1, channel=2,
            raw_value=5000, exponent=0,
        )
        sensor._handle_measurement_update(other_meas)
        assert len(updated) == 0
