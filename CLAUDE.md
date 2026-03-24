# CLAUDE.md — Project Context for SpaceLogic C-Gate

## What This Is

A Home Assistant custom integration (`spacelogic_cgate`) that controls Clipsal/Schneider Electric C-Bus building automation systems via a C-Gate TCP server.

## Repository Layout

```
custom_components/spacelogic_cgate/   # The HA integration
  __init__.py          # Entry setup, platform forwarding
  cgate.py             # Core TCP client (CGateClient, CGateGroup, CGateMeasurement)
  config_flow.py       # Two-step config flow + options flow
  const.py             # All constants, ports, group types, unit codes
  light.py             # Light platform (dimmer + relay)
  switch.py            # Switch platform
  cover.py             # Cover platform (blinds/shutters)
  fan.py               # Fan platform
  lock.py              # Lock platform
  valve.py             # Valve platform
  sensor.py            # Measurement sensor platform
  manifest.json        # HA integration manifest
  strings.json         # UI strings
  translations/en.json # English translations
tests/                 # pytest-based tests
  conftest.py          # Fixtures and mocks
  test_cgate.py        # CGateClient unit tests
  test_entities.py     # Entity platform tests
  test_light.py        # Light-specific tests
hacs.json              # HACS store metadata
```

## Architecture

### TCP Communication
CGateClient manages 3 async TCP connections to C-Gate:
- **Command port** (20023): Request/response commands (`GET`, `ON`, `OFF`, `RAMP`)
- **Event port** (20024): System events (currently lightly used)
- **Status Change port** (20025): Real-time SCP events for group level changes and measurement data

### Key Patterns
- **Group discovery**: `DBGETXML` command returns XML with all configured groups. Parsed by `parse_xml_groups()`.
- **Entity type mapping**: Groups default to dimmer. Users override via `entry.options[CONF_GROUP_OVERRIDES]` dict mapping `unique_id` → group type string.
- **Virtual groups**: Groups without physical units. `GET level` returns 401. Flagged as `is_virtual=True` and excluded from future polling.
- **Measurement sensors**: C-Bus app 228. Events carry raw value, exponent, and unit code. Computed value = `raw_value × 10^exponent`.
- **Area matching**: Group tag names are fuzzy-matched against existing HA areas (longest substring match wins).
- **SCP event parsing**: Regex-based parsing of status change events in `_process_scp_event()`.

### C-Bus Address Format
Groups are addressed as `//PROJECT/NETWORK/APPLICATION/GROUP` (e.g., `//MYPROJECT/254/56/10`).
- Network is typically 254
- Application 56 = Lighting, 228 = Measurement

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Lint
ruff check .

# Type check
mypy custom_components/spacelogic_cgate/
```

## Key Decisions
- **No external dependencies**: Pure Python async TCP using `asyncio.open_connection`. No pip requirements.
- **GPL-3.0 license**: Matches C-Bus ecosystem conventions.
- **IoT class `local_push`**: Integration receives real-time events; no cloud dependency.
- **Config flow only**: No YAML configuration. Setup entirely through the UI.
- **Single C-Gate project per entry**: One integration entry = one C-Gate project connection.

## Known Behaviors
- SCP events for groups set to level 0 won't retrigger until C-Gate is restarted (C-Gate protocol limitation).
- Virtual group detection is self-healing: on C-Gate restart, the first `GET level` will 401 again and re-flag.
- Keepalive sends `NOOP` every 60 seconds; reconnects after 15-second delay on connection loss.
