"""Constants for the SpaceLogic C-Gate integration."""

DOMAIN = "spacelogic_cgate"

# C-Gate default TCP ports
DEFAULT_COMMAND_PORT = 20023
DEFAULT_EVENT_PORT = 20024
DEFAULT_STATUS_CHANGE_PORT = 20025
DEFAULT_CONFIG_CHANGE_PORT = 20026

# C-Gate default host
DEFAULT_HOST = "192.168.11.24"

# C-Gate default project
DEFAULT_PROJECT_NAME = "YELMAH"

# C-Gate connection
CONF_COMMAND_PORT = "command_port"
CONF_EVENT_PORT = "event_port"
CONF_STATUS_CHANGE_PORT = "status_change_port"
CONF_PROJECT_NAME = "project_name"

# C-Gate response codes
RESPONSE_OK = 200
RESPONSE_SERVICE_READY = 201
RESPONSE_OBJECT_INFO = 300
# "Bad object or device ID" -- what a group with no physical unit answers to
# GET level. Permanent for that group, unlike every other error code.
RESPONSE_NO_SUCH_OBJECT = 401

# C-Gate keepalive
DEFAULT_KEEPALIVE_INTERVAL = 60  # seconds
RECONNECT_DELAY = 15  # seconds — first retry delay, then doubles
RECONNECT_DELAY_MAX = 120  # seconds — backoff ceiling

# Measurement application (228) polling
DEFAULT_MEASUREMENT_SCAN_INTERVAL = 30  # seconds between measurement polls
DEFAULT_MEASUREMENT_STALE_AFTER = 300  # seconds before a channel reads unavailable
MEASUREMENT_SCAN_MAX_DEVICE = 15
MEASUREMENT_SCAN_MAX_CHANNEL = 7
DEFAULT_MEASUREMENT_NETWORK = 254

# C-Bus applications
CBUS_LIGHTING_APPLICATION = 56  # 0x38
CBUS_MEASUREMENT_APPLICATION = 228  # 0xE4

# C-Bus level range
CBUS_LEVEL_MIN = 0
CBUS_LEVEL_MAX = 255

# Platforms
PLATFORMS: list[str] = ["light"]

# Group type options (stored in entry.options)
CONF_GROUP_OVERRIDES = "group_overrides"
GROUP_TYPE_DIMMER = "light_dimmer"
GROUP_TYPE_RELAY = "light_relay"
GROUP_TYPE_SWITCH = "switch"
GROUP_TYPE_COVER = "cover"
GROUP_TYPE_LOCK = "lock"
GROUP_TYPE_FAN = "fan"
GROUP_TYPE_VALVE = "valve"
DEFAULT_GROUP_TYPE = GROUP_TYPE_DIMMER

# C-Bus Measurement Application unit codes (from Section 4.7.7.5)
# Maps the integer unit code from measurement events to (unit_name, typical_use)
UNIT_CODE_CELSIUS = 0x00          # °C — Temperature
UNIT_CODE_AMPS = 0x01             # Amps — Current
UNIT_CODE_DEGREES = 0x02          # Angle (degrees)
UNIT_CODE_COULOMB = 0x03          # Coulomb — Charge
UNIT_CODE_BOOLEAN = 0x04          # Boolean
UNIT_CODE_FARADS = 0x05           # Farads — Capacitance
UNIT_CODE_HENRYS = 0x06           # Henrys — Inductance
UNIT_CODE_HERTZ = 0x07            # Hertz — Frequency
UNIT_CODE_JOULES = 0x08           # Joules — Energy
UNIT_CODE_KATAL = 0x09            # Katal
UNIT_CODE_KG_PER_M3 = 0x0A       # kg/m³ — Density
UNIT_CODE_KILOGRAMS = 0x0B        # Kilograms — Mass
UNIT_CODE_LITRES = 0x0C           # Litres — Volume
UNIT_CODE_LITRES_PER_HOUR = 0x0D  # L/h — Slow flow
UNIT_CODE_LITRES_PER_MIN = 0x0E   # L/min — Flow
UNIT_CODE_LITRES_PER_SEC = 0x0F   # L/s — Flow
UNIT_CODE_LUX = 0x10              # Lux — Light level
UNIT_CODE_METRES = 0x11           # Metres — Distance
UNIT_CODE_METRES_PER_MIN = 0x12   # m/min — Speed
UNIT_CODE_METRES_PER_SEC = 0x13   # m/s — Speed
UNIT_CODE_METRES_PER_SEC2 = 0x14  # m/s² — Acceleration
UNIT_CODE_MOLE = 0x15             # Mole
UNIT_CODE_NEWTON_METRE = 0x16     # Nm — Torque
UNIT_CODE_NEWTONS = 0x17          # N — Force
UNIT_CODE_OHMS = 0x18             # Ohms — Resistance
UNIT_CODE_PASCAL = 0x19           # Pascal — Pressure
UNIT_CODE_PERCENT = 0x1A          # % — Humidity / generic
UNIT_CODE_DECIBELS = 0x1B         # dB
UNIT_CODE_PPM = 0x1C              # PPM — Concentration
UNIT_CODE_RPM = 0x1D              # RPM — Angular speed
UNIT_CODE_SECONDS = 0x1E          # Seconds
UNIT_CODE_MINUTES = 0x1F          # Minutes
UNIT_CODE_HOURS = 0x20            # Hours
UNIT_CODE_SIEVERTS = 0x21         # Sv — Radiation
UNIT_CODE_STERADIAN = 0x22        # sr — Solid angle
UNIT_CODE_TESLA = 0x23            # T — Magnetic field
UNIT_CODE_VOLTS = 0x24            # V — Voltage
UNIT_CODE_WATT_HOURS = 0x25       # Wh — Energy
UNIT_CODE_WATTS = 0x26            # W — Power
UNIT_CODE_WEBERS = 0x27           # Wb — Magnetic flux
UNIT_CODE_NO_UNITS = 0xFE         # No units
UNIT_CODE_CUSTOM = 0xFF           # Custom
