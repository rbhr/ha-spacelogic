"""Sensor platform for SpaceLogic C-Gate integration (Measurement Application)."""

from __future__ import annotations

import logging

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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CGateConfigEntry
from .cgate import CGateClient, CGateMeasurement
from .const import (
    DOMAIN,
    UNIT_CODE_AMPS,
    UNIT_CODE_CELSIUS,
    UNIT_CODE_HERTZ,
    UNIT_CODE_LUX,
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
        """Return True if the C-Gate connection is active."""
        return self._client.connected

    @callback
    def _handle_measurement_update(self, meas: CGateMeasurement) -> None:
        """Handle a measurement update for this sensor."""
        if meas.unique_id == self._measurement.unique_id:
            self.async_write_ha_state()

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        """Refresh availability when the C-Gate link comes or goes.

        Nothing else writes this entity's state on an outage, so without it the
        sensor would keep presenting a reading it can no longer verify.
        """
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
