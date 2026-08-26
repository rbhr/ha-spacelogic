# SpaceLogic C-Gate

[![HACS Validation](https://github.com/rbhr/ha-spacelogic/actions/workflows/hacs.yml/badge.svg)](https://github.com/rbhr/ha-spacelogic/actions/workflows/hacs.yml)
[![Hassfest Validation](https://github.com/rbhr/ha-spacelogic/actions/workflows/hassfest.yml/badge.svg)](https://github.com/rbhr/ha-spacelogic/actions/workflows/hassfest.yml)
[![License: GPL v3](https://img.shields.io/github/license/rbhr/ha-spacelogic)](LICENSE)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A [Home Assistant](https://www.home-assistant.io/) custom integration for **Clipsal/Schneider Electric SpaceLogic C-Bus** systems via a [C-Gate server](https://updates.clipsal.com/ClipsalSoftwareDownload/mainsite/cis/technical/downloads/c-gate.html).

## Overview

This integration connects Home Assistant to C-Bus building automation networks using C-Gate as a bridge. It communicates over TCP with C-Gate's command, event, and status-change ports to provide real-time control and monitoring of C-Bus groups.

Groups are auto-discovered from the C-Gate project database. Each group can be mapped to the appropriate Home Assistant entity type during setup via a config-flow UI.

## Features

- **Auto-discovery** of C-Bus lighting groups from C-Gate project database (DBGETXML)
- **Real-time status updates** via C-Gate's Status Change Protocol (SCP)
- **Seven entity platforms** covering the most common C-Bus group types:

| Platform | C-Bus Use Case | Capabilities |
|----------|---------------|--------------|
| **Light (Dimmer)** | Dimmable luminaires | Brightness 0–100%, transition ramp rates |
| **Light (Relay)** | Switched luminaires | On/off |
| **Switch** | General-purpose relays | On/off toggle |
| **Cover** | Blinds, shutters | Open/close, position 0–100% |
| **Fan** | Ceiling fans, ventilation | On/off, speed percentage |
| **Lock** | Access control | Lock/unlock |
| **Valve** | Irrigation, HVAC valves | Open/close |

- **Measurement sensors** for the C-Bus Measurement Application (application 228), supporting:
  - Temperature, humidity, illuminance, pressure
  - Voltage, current, power, energy
  - And many more (30+ unit types from the C-Bus specification)
- **Area auto-matching** — groups are suggested to Home Assistant areas by matching C-Bus tag names
- **Per-group type overrides** — reassign any group to a different entity type via the Options flow
- **Reconfigurable connection** — change the C-Gate host, ports or project without losing entity IDs or history
- **Virtual group detection** — groups without physical units are automatically detected and excluded from polling
- **Keepalive & auto-reconnect** — maintains persistent TCP connections with automatic recovery

## Requirements

- A running **C-Gate server** (v2.x or later) accessible over the network
- A configured **C-Bus project** with at least one C-Bus network
- **Home Assistant** 2024.1.0 or later

## Installation

### HACS (Recommended)

1. Open **HACS** in Home Assistant
2. Click the three dots menu (top right) and select **Custom repositories**
3. Add `https://github.com/rbhr/ha-spacelogic` with category **Integration**
4. Search for "SpaceLogic C-Gate" and click **Download**
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/spacelogic_cgate` directory into your Home Assistant `custom_components/` folder
2. Restart Home Assistant

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **SpaceLogic C-Gate**
3. Enter your C-Gate server connection details:
   - **Host** — IP address or hostname of the C-Gate server
   - **Command Port** — default `20023`
   - **Event Port** — default `20024`
   - **Status Change Port** — default `20025`
   - **Project Name** — your C-Gate project name
4. The integration will connect, discover all groups, and present a type-assignment screen
5. For each discovered group, select the appropriate entity type (dimmer, relay, switch, cover, fan, lock, or valve)
6. Click **Submit** to finish setup

### Changing the C-Gate Server Address

If your C-Gate server moves to a new IP address (or you change a port or the
project name):

1. Go to **Settings > Devices & Services**
2. Find the SpaceLogic C-Gate integration, open its menu (**⋮**) and click **Reconfigure**
3. Update the connection details and submit

The new details are tested before they are saved, and the existing config entry
is updated in place — every entity keeps its entity ID, its history and its
group type assignment, and devices stay where they are. Deleting and re-adding
the integration would *not* preserve these, so use Reconfigure instead.

### Reconfiguring Group Types

To change a group's entity type after initial setup:

1. Go to **Settings > Devices & Services**
2. Find the SpaceLogic C-Gate integration and click **Configure**
3. Update group type assignments and submit

## How It Works

The integration opens three TCP connections to C-Gate:

- **Command port** (20023) — sends control commands and queries
- **Event port** (20024) — receives system events
- **Status Change port** (20025) — receives real-time group level changes and measurement data

On startup, it requests the project database XML (`DBGETXML`) to discover all configured lighting groups and their metadata (name, tag/area). It then registers for status-change events to receive real-time updates whenever a C-Bus group level changes on the network.

## Contributing

Contributions are welcome! Please open an [issue](https://github.com/rbhr/ha-spacelogic/issues) or submit a pull request.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
