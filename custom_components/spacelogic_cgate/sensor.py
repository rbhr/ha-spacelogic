"""Sensor platform for SpaceLogic C-Gate integration (Measurement Application)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import CGateConfigEntry
from .cgate import CGateClient, CGateConnectionError, CGateMeasurement
from .const import (
    CBUS_MEASUREMENT_APPLICATION,
    DEFAULT_MEASUREMENT_SCAN_INTERVAL,
    DEFAULT_MEASUREMENT_STALE_AFTER,
    DOMAIN,
    UNIT_CODE_AMPS,
    UNIT_CODE_CELSIUS,
    UNIT_CODE_HERTZ,
    UNIT_CODE_LUX,
    UNIT_CODE_OHMS,
    UNIT_CODE_PASCAL,
    UNIT_CODE_PERCENT,
    UNIT_CODE_VOLTS,
    UNIT_CODE_WATT_HOURS,
    UNIT_CODE_WATTS,
)

_LOGGER = logging.getLogger(__name__)

# Metadata tuple: (type_label, device_class, unit, state_class)
_MeasMeta = tuple[
    str,
    SensorDeviceClass | None,
    str | None,
    SensorStateClass | None,
]

# Map C-Bus unit code to HA sensor metadata.
# Only codes with a natural HA device_class mapping are listed;
# unknown codes get a generic sensor with the raw value.
_UNIT_CODE_META: dict[int, _MeasMeta] = {
    # HA 2026.8.3 has no SensorDeviceClass.RESISTANCE, so device_class stays
    # None. Without this entry these channels render as "CH2 Unit 24".
    UNIT_CODE_OHMS: (
        "Resistance",
        None,
        "Ω",
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_CELSIUS: (
        "Temperature",
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_AMPS: (
        "Current",
        SensorDeviceClass.CURRENT,
        UnitOfElectricCurrent.AMPERE,
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_HERTZ: (
        "Frequency",
        SensorDeviceClass.FREQUENCY,
        "Hz",
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_LUX: (
        "Illuminance",
        SensorDeviceClass.ILLUMINANCE,
        "lx",
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_PASCAL: (
        "Pressure",
        SensorDeviceClass.PRESSURE,
        UnitOfPressure.PA,
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_PERCENT: (
        "Humidity",
        SensorDeviceClass.HUMIDITY,
        "%",
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_VOLTS: (
        "Voltage",
        SensorDeviceClass.VOLTAGE,
        UnitOfElectricPotential.VOLT,
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_CODE_WATT_HOURS: (
        "Energy",
        SensorDeviceClass.ENERGY,
        UnitOfEnergy.WATT_HOUR,
        SensorStateClass.TOTAL_INCREASING,
    ),
    UNIT_CODE_WATTS: (
        "Power",
        SensorDeviceClass.POWER,
        UnitOfPower.WATT,
        SensorStateClass.MEASUREMENT,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CGateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up C-Gate measurement sensors from a config entry."""
    client: CGateClient = entry.runtime_data
    known_measurements: set[str] = set()

    # Create sensors for any measurements already received
    entities = []
    for uid, meas in client.measurements.items():
        if uid not in known_measurements:
            known_measurements.add(uid)
            entities.append(CGateMeasurementSensor(client, meas, entry.entry_id))
    if entities:
        async_add_entities(entities)

    @callback
    def _handle_measurement(meas: CGateMeasurement) -> None:
        """Handle a measurement update — create entity if new."""
        if meas.unique_id not in known_measurements:
            known_measurements.add(meas.unique_id)
            async_add_entities(
                [CGateMeasurementSensor(client, meas, entry.entry_id)]
            )

    unsub = client.register_measurement_callback(_handle_measurement)
    entry.async_on_unload(unsub)

    # One shared poll task, not per-entity async_update. Every command already
    # serialises behind the client's single _cmd_lock, and ~150 group polls
    # already run on HA's 30s cycle; 14 concurrent measurement pollers would
    # queue ahead of user-initiated commands such as a light switch press.
    poll_lock = asyncio.Lock()

    async def _poll(_now: datetime | None = None) -> None:
        if not client.connected or poll_lock.locked():
            return
        async with poll_lock:
            # Include channels discovered since setup, even if their entity
            # registry entry has not been created yet.
            channels = _known_channels(hass, entry)
            if channels:
                with contextlib.suppress(CGateConnectionError):
                    await client.async_refresh_measurements(channels)

    entry.async_on_unload(
        async_track_time_interval(
            hass, _poll, timedelta(seconds=DEFAULT_MEASUREMENT_SCAN_INTERVAL)
        )
    )

    @callback
    def _on_connection(connected: bool) -> None:
        """Refresh immediately on reconnect rather than waiting a full cycle."""
        if connected:
            entry.async_create_background_task(
                hass, _poll(), "cgate_measurement_refresh"
            )

    entry.async_on_unload(client.register_connection_callback(_on_connection))


def _known_channels(
    hass: HomeAssistant, entry: CGateConfigEntry, *, include_live: bool = True
) -> list[tuple[int, int, int]]:
    """Combine persistent channels with readings discovered during this session.

    The registry already persists exactly which channels exist, so this needs no
    extra storage and no probe on a normal start. Note the integration's own
    DBGETXML-based discovery cannot help here: it walks a four-level tree, while
    a measurement channel is five levels deep.
    """
    client: CGateClient | None = getattr(entry, "runtime_data", None)
    channels: set[tuple[int, int, int]] = {
        (meas.network, meas.device, meas.channel)
        for meas in client.measurements.values()
        if meas.application == CBUS_MEASUREMENT_APPLICATION
    } if client and include_live else set()
    prefix = f"{entry.entry_id}_meas_"
    for reg_entry in er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    ):
        if not reg_entry.unique_id.startswith(prefix):
            continue
        parts = reg_entry.unique_id[len(prefix):].split("_")
        if len(parts) != 4:
            continue
        try:
            network, application, device, channel = (int(p) for p in parts)
        except ValueError:
            continue
        if application == CBUS_MEASUREMENT_APPLICATION:
            channels.add((network, device, channel))
    return sorted(channels)


class CGateMeasurementSensor(SensorEntity):
    """Representation of a C-Bus measurement channel as a sensor."""

    _attr_has_entity_name = False

    def __init__(
        self,
        client: CGateClient,
        measurement: CGateMeasurement,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        self._client = client
        self._measurement = measurement
        self._entry_id = entry_id

        # Derive sensor metadata from the C-Bus unit code
        meta = _UNIT_CODE_META.get(measurement.units)
        if meta:
            type_name, device_class, unit, state_class = meta
            self._attr_device_class = device_class
            self._attr_native_unit_of_measurement = unit
            self._attr_state_class = state_class
        else:
            type_name = f"Unit {measurement.units}"

        self._attr_unique_id = f"{entry_id}_meas_{measurement.unique_id}"
        self._attr_name = f"CH{measurement.channel} {type_name}"
        self.entity_id = (
            f"sensor.sl_meas_{measurement.device}_{measurement.channel}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info grouping sensors by measurement device."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self._entry_id}_meas_dev_{self._measurement.network}"
                    f"_{self._measurement.device}",
                )
            },
            name=f"Measurement Device {self._measurement.device}",
            manufacturer="Schneider Electric",
            model="C-Bus Measurement Device",
            via_device=(DOMAIN, self._entry_id),
        )

    @property
    def native_value(self) -> float | None:
        """Return the current measurement value."""
        return self._measurement.value

    @property
    def available(self) -> bool:
        """Return True if connected and the channel has reported recently.

        Staleness matters because a frozen reading is worse than a missing one:
        nothing downstream can tell it is dead. Caveat: a poll returns C-Gate's
        cached value, so this catches C-Gate down / channel gone / poll loop
        dead, but not a failed physical sensor behind a live C-Gate.
        """
        return (
            self._client.connected
            and self._measurement.age < DEFAULT_MEASUREMENT_STALE_AFTER
        )

    @callback
    def _handle_measurement_update(self, meas: CGateMeasurement) -> None:
        """Handle a measurement update for this sensor."""
        if meas.unique_id == self._measurement.unique_id:
            self.async_write_ha_state()

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        """Reflect connect/disconnect immediately rather than at the next poll."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for measurement updates when entity is added."""
        self.async_on_remove(
            self._client.register_measurement_callback(
                self._handle_measurement_update
            )
        )
        self.async_on_remove(
            self._client.register_connection_callback(
                self._handle_connection_change
            )
        )
