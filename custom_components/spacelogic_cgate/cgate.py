"""C-Gate TCP client for communicating with a C-Gate server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field

from .const import (
    CBUS_LIGHTING_APPLICATION,
    CBUS_MEASUREMENT_APPLICATION,
    DEFAULT_KEEPALIVE_INTERVAL,
    RESPONSE_SERVICE_READY,
)

_LOGGER = logging.getLogger(__name__)

# Pattern to parse C-Gate response lines: "CODE rest-of-line"
RESPONSE_PATTERN = re.compile(r"^(\d{3})\s*(.*)")

# Pattern to parse SCP lighting events:
#   lighting on //PROJECT/NET/APP/GROUP #sourceunit=N
#   lighting off //PROJECT/NET/APP/GROUP #sourceunit=N
#   lighting ramp //PROJECT/NET/APP/GROUP LEVEL #sourceunit=N
SCP_LIGHTING_PATTERN = re.compile(
    r"^lighting\s+(on|off|ramp)\s+//(\S+?)/(\d+)/(\d+)/(\d+)"
    r"(?:\s+(\d+)%?)?"
)

# Pattern to parse level response: "300 //PROJECT/NET/APP/GROUP: level=VALUE"
LEVEL_RESPONSE_PATTERN = re.compile(
    r"^300\s+\S+:\s+level=(\d+)"
)

# Pattern to parse SCP measurement events:
#   measurement data //PROJECT/NET/228/CHANNEL/TYPE VALUE EXPONENT FLAGS #sourceunit=N OID=
SCP_MEASUREMENT_PATTERN = re.compile(
    r"^measurement\s+data\s+//(\S+?)/(\d+)/(\d+)/(\d+)/(\d+)"
    r"\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)"
    r"(?:\s+#sourceunit=(\d+))?"
)

# C-Gate DBGETXML response codes
RESPONSE_XML_BEGIN = 343
RESPONSE_XML_CONTENT = 347
RESPONSE_XML_END = 344

# Tag names to filter out (not real groups)
IGNORED_TAG_NAMES = frozenset({"<unused>", "unused", "untitled", ""})

# Buffer limit for the command port StreamReader.
# DBGETXML can return large XML payloads (hundreds of KB) that exceed
# asyncio's default 64KB line limit.
CMD_BUFFER_LIMIT = 1024 * 1024  # 1 MB


class CGateConnectionError(Exception):
    """Raised when unable to connect to C-Gate server."""


class CGateCommandError(Exception):
    """Raised when a C-Gate command fails."""


@dataclass
class CGateGroup:
    """Represents a C-Bus group (e.g., a lighting group)."""

    network: int
    application: int
    group: int
    level: int = 0
    name: str = ""
    is_virtual: bool = False

    @property
    def address(self) -> str:
        """Return the C-Gate address string for this group."""
        return f"{self.network}/{self.application}/{self.group}"

    @property
    def unique_id(self) -> str:
        """Return a unique identifier for this group."""
        return f"{self.network}_{self.application}_{self.group}"


@dataclass
class CGateMeasurement:
    """Represents a C-Bus measurement channel data point.

    Address hierarchy: //PROJECT/NETWORK/228/DEVICE/CHANNEL
    The 'units' field is the C-Bus unit code (Section 4.7.7.5) that
    identifies what is being measured (e.g. 0x00=°C, 0x26=Watts).
    """

    network: int
    application: int  # 228 for measurement
    device: int  # measurement device number (0-255)
    channel: int  # channel within the device (0-255)
    raw_value: int = 0
    exponent: int = 0
    units: int = 0  # C-Bus unit code from event (see const.py UNIT_CODE_*)
    source_unit: int = 0

    @property
    def value(self) -> float:
        """Return the computed measurement value (raw_value × 10^exponent)."""
        return self.raw_value * (10 ** self.exponent)

    @property
    def unique_id(self) -> str:
        """Return a unique identifier for this measurement."""
        return f"{self.network}_{self.application}_{self.device}_{self.channel}"


def _element_value(element: ET.Element, key: str) -> str | None:
    """Get a value from an element, checking both XML attributes and child elements.

    C-Gate DBGETXML uses child elements (e.g., <Address>254</Address>),
    but we also support XML attributes for flexibility.
    """
    # Check XML attributes first (fast path)
    val = element.get(key)
    if val is not None:
        return val.strip()
    # Check child elements with common casings
    for tag in (key, key.lower(), key.capitalize()):
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return None


def parse_xml_groups(
    xml_text: str, application: int = CBUS_LIGHTING_APPLICATION
) -> list[dict[str, int | str]]:
    """Parse C-Gate DBGETXML response XML and extract lighting groups.

    Returns a list of dicts with keys: network, application, group, name.
    The XML structure is:
      Installation > Project > Network > Application > Group
    where Address and TagName are child elements (not XML attributes).
    """
    results: list[dict[str, int | str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        _LOGGER.warning("Failed to parse C-Gate XML database")
        return results

    for project_el in root:
        if project_el.tag.lower() != "project":
            continue
        for network_el in project_el:
            if network_el.tag.lower() != "network":
                continue
            network_addr = _element_value(network_el, "Address")
            if network_addr is None:
                continue
            try:
                network_num = int(network_addr)
            except ValueError:
                continue

            for app_el in network_el:
                if app_el.tag.lower() != "application":
                    continue
                app_addr = _element_value(app_el, "Address")
                if app_addr is None:
                    continue
                try:
                    app_num = int(app_addr)
                except ValueError:
                    continue

                if app_num != application:
                    continue

                for group_el in app_el:
                    if group_el.tag.lower() != "group":
                        continue
                    group_addr = _element_value(group_el, "Address")
                    tag_name = _element_value(group_el, "TagName") or ""
                    if group_addr is None:
                        continue

                    try:
                        group_num = int(group_addr)
                    except ValueError:
                        continue

                    # Skip group 255 and unnamed/placeholder groups
                    if group_num == 255:
                        continue
                    if tag_name.lower().strip() in IGNORED_TAG_NAMES:
                        continue
                    # Skip default tag names like "Group 99" that match
                    # the group address — means unconfigured in C-Bus
                    if tag_name.strip().lower() == f"group {group_num}":
                        continue

                    results.append({
                        "network": network_num,
                        "application": app_num,
                        "group": group_num,
                        "name": tag_name.strip(),
                    })

    return results


@dataclass
class CGateClient:
    """Client for communicating with a C-Gate server over TCP.

    Manages three connections:
    - Command port (20023): send commands, receive responses
    - Event port (20024): receive system events
    - Status Change port (20025): receive real-time status updates
    """

    host: str
    command_port: int
    event_port: int
    status_change_port: int
    project_name: str

    _cmd_reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _cmd_writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    _scp_reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _scp_writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    _event_reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _event_writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    _cmd_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _keepalive_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _scp_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _event_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _connected: bool = field(default=False, repr=False)
    _groups: dict[str, CGateGroup] = field(default_factory=dict, repr=False)
    _status_callbacks: list[Callable[[CGateGroup], None]] = field(
        default_factory=list, repr=False
    )
    _measurements: dict[str, CGateMeasurement] = field(
        default_factory=dict, repr=False
    )
    _measurement_callbacks: list[Callable[[CGateMeasurement], None]] = field(
        default_factory=list, repr=False
    )

    @property
    def connected(self) -> bool:
        """Return whether the client is connected."""
        return self._connected

    @property
    def groups(self) -> dict[str, CGateGroup]:
        """Return discovered groups keyed by unique_id."""
        return self._groups

    @property
    def measurements(self) -> dict[str, CGateMeasurement]:
        """Return discovered measurements keyed by unique_id."""
        return self._measurements

    def register_measurement_callback(
        self, callback: Callable[[CGateMeasurement], None]
    ) -> Callable[[], None]:
        """Register a callback for measurement updates. Returns unsubscribe function."""
        self._measurement_callbacks.append(callback)

        def unsubscribe() -> None:
            self._measurement_callbacks.remove(callback)

        return unsubscribe

    def register_status_callback(
        self, callback: Callable[[CGateGroup], None]
    ) -> Callable[[], None]:
        """Register a callback for status changes. Returns unsubscribe function."""
        self._status_callbacks.append(callback)

        def unsubscribe() -> None:
            self._status_callbacks.remove(callback)

        return unsubscribe

    async def connect(self) -> None:
        """Connect to the C-Gate server."""
        try:
            # Connect command port (large buffer for DBGETXML responses)
            cmd_reader, cmd_writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host, self.command_port, limit=CMD_BUFFER_LIMIT
                ),
                timeout=10,
            )
            self._cmd_reader, self._cmd_writer = cmd_reader, cmd_writer
            # Wait for 201 Service Ready
            greeting = await asyncio.wait_for(cmd_reader.readline(), timeout=10)
            greeting_text = greeting.decode("ascii", errors="replace").strip()
            if not greeting_text.startswith(str(RESPONSE_SERVICE_READY)):
                raise CGateConnectionError(
                    f"Unexpected greeting from C-Gate: {greeting_text}"
                )
            _LOGGER.debug("C-Gate command connected: %s", greeting_text)

            # Connect status change port
            self._scp_reader, self._scp_writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.status_change_port),
                timeout=10,
            )
            _LOGGER.debug("C-Gate SCP connected")

            # Connect event port
            self._event_reader, self._event_writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.event_port),
                timeout=10,
            )
            _LOGGER.debug("C-Gate event port connected")

            self._connected = True

            # Set up project
            await self._send_command(f"PROJECT USE {self.project_name}")
            await self._send_command(f"PROJECT START {self.project_name}")

            # Enable events on command session for inline monitoring
            await self._send_command("EVENT e5s1c1")

            # Start background listeners
            self._scp_task = asyncio.create_task(self._scp_listener())
            self._event_task = asyncio.create_task(self._event_listener())
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

        except (TimeoutError, OSError) as err:
            await self.disconnect()
            raise CGateConnectionError(
                f"Failed to connect to C-Gate at {self.host}:{self.command_port}"
            ) from err

    async def disconnect(self) -> None:
        """Disconnect from the C-Gate server."""
        self._connected = False

        for task in (self._keepalive_task, self._scp_task, self._event_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._keepalive_task = None
        self._scp_task = None
        self._event_task = None

        for writer in (self._cmd_writer, self._scp_writer, self._event_writer):
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
        self._cmd_writer = None
        self._scp_writer = None
        self._event_writer = None
        self._cmd_reader = None
        self._scp_reader = None
        self._event_reader = None

        _LOGGER.debug("C-Gate client disconnected")

    async def _send_receive(self, command: str) -> list[str]:
        """Send a command and return all response lines.

        Must be called with _cmd_lock held.
        Raises CGateCommandError on failure responses (4xx, 5xx).
        """
        if not self._cmd_writer or not self._cmd_reader:
            raise CGateConnectionError("Not connected to C-Gate")

        _LOGGER.debug("C-Gate TX: %s", command)
        self._cmd_writer.write(f"{command}\r\n".encode("ascii"))
        await self._cmd_writer.drain()

        response_lines: list[str] = []
        while True:
            line = await asyncio.wait_for(
                self._cmd_reader.readline(), timeout=30
            )
            text = line.decode("ascii", errors="replace").strip()
            if not text:
                continue

            _LOGGER.debug("C-Gate RX: %s", text)
            response_lines.append(text)

            match = RESPONSE_PATTERN.match(text)
            if match and text[3:4] != "-":
                code = int(match.group(1))
                if code >= 400:
                    raise CGateCommandError(
                        f"C-Gate error {code}: {match.group(2)}"
                    )
                return response_lines

        return response_lines

    async def _send_command(self, command: str) -> str:
        """Send a command and return the final response line."""
        async with self._cmd_lock:
            lines = await self._send_receive(command)
            return lines[-1] if lines else ""

    async def turn_on(self, group: CGateGroup) -> None:
        """Turn on a C-Bus group."""
        await self._send_command(f"ON {group.address}")
        group.level = 255

    async def turn_off(self, group: CGateGroup) -> None:
        """Turn off a C-Bus group."""
        await self._send_command(f"OFF {group.address}")
        group.level = 0

    async def ramp(
        self, group: CGateGroup, level: int, transition: int | None = None
    ) -> None:
        """Ramp a C-Bus group to a level (0-255) with optional transition time in seconds."""
        cmd = f"RAMP {group.address} {level}"
        if transition is not None:
            cmd += f" {transition}s"
        await self._send_command(cmd)
        group.level = level

    async def get_level(self, group: CGateGroup) -> int:
        """Get the current level of a C-Bus group.

        Virtual groups (no physical unit) return a 401 error from C-Gate.
        When this happens, the group is marked as virtual so future polls
        skip the command entirely.
        """
        if group.is_virtual:
            group.level = 0
            return 0
        try:
            response = await self._send_command(f"GET {group.address} level")
        except CGateCommandError:
            group.level = 0
            group.is_virtual = True
            _LOGGER.debug(
                "Group %s returned error on GET level; marking as virtual",
                group.address,
            )
            return group.level
        match = LEVEL_RESPONSE_PATTERN.match(response)
        if match:
            group.level = int(match.group(1))
        return group.level

    async def _fetch_xml_groups(
        self, application: int
    ) -> list[dict[str, int | str]]:
        """Fetch and parse groups from C-Gate DBGETXML for a given application.

        Issues a single DBGETXML command to retrieve the entire project's
        XML tag database, then parses it for groups with tag names.

        Response protocol:
        - 343: Begin XML snippet
        - 347: XML content line (may be many)
        - 344: End XML snippet
        """
        async with self._cmd_lock:
            try:
                lines = await self._send_receive(
                    f"DBGETXML //{self.project_name}"
                )
            except CGateCommandError:
                _LOGGER.warning("DBGETXML failed for project %s", self.project_name)
                return []

        # Extract XML content from 347 lines.
        # Response lines use "347-content" (continuation) format.
        # After RESPONSE_PATTERN matches "347", the remainder starts with
        # "-" for continuation lines — strip it to get the actual XML.
        xml_parts: list[str] = []
        for line in lines:
            match = RESPONSE_PATTERN.match(line)
            if match:
                code = int(match.group(1))
                if code == RESPONSE_XML_CONTENT:
                    content = match.group(2)
                    if content.startswith("-"):
                        content = content[1:]
                    xml_parts.append(content)

        if not xml_parts:
            _LOGGER.warning("DBGETXML returned no XML content")
            return []

        xml_text = "\n".join(xml_parts)
        _LOGGER.debug("DBGETXML returned %d bytes of XML", len(xml_text))
        _LOGGER.debug("DBGETXML XML start: %.500s", xml_text)

        return parse_xml_groups(xml_text, application)

    async def discover_lighting_groups(
        self, application: int = CBUS_LIGHTING_APPLICATION
    ) -> list[CGateGroup]:
        """Discover all lighting groups via DBGETXML."""
        group_defs = await self._fetch_xml_groups(application)

        # Create CGateGroup objects and fetch current levels
        discovered: list[CGateGroup] = []
        for gdef in group_defs:
            group = self._get_or_create_group(
                int(gdef["network"]), int(gdef["application"]), int(gdef["group"])
            )
            group.name = str(gdef["name"])
            discovered.append(group)

        _LOGGER.info(
            "Discovered %d lighting groups from C-Gate XML database",
            len(discovered),
        )

        # Fetch current levels for discovered groups
        for group in discovered:
            try:
                await self.get_level(group)
            except (CGateCommandError, CGateConnectionError):
                _LOGGER.debug(
                    "Could not get level for group %s", group.address
                )

        return discovered

    async def discover_measurement_channels(self) -> list[CGateGroup]:
        """Discover measurement channels via DBGETXML.

        Returns CGateGroup objects for application 228 channels.
        These are NOT stored in self._groups to avoid mixing with lighting groups.
        """
        group_defs = await self._fetch_xml_groups(CBUS_MEASUREMENT_APPLICATION)

        channels: list[CGateGroup] = []
        for gdef in group_defs:
            channels.append(CGateGroup(
                network=int(gdef["network"]),
                application=int(gdef["application"]),
                group=int(gdef["group"]),
                name=str(gdef["name"]),
            ))

        _LOGGER.info(
            "Discovered %d measurement channels from C-Gate XML database",
            len(channels),
        )
        return channels

    async def _keepalive_loop(self) -> None:
        """Send periodic NOOP commands to keep the connection alive."""
        while self._connected:
            try:
                await asyncio.sleep(DEFAULT_KEEPALIVE_INTERVAL)
                if self._connected:
                    await self._send_command("NOOP")
            except CGateCommandError:
                _LOGGER.warning("Keepalive NOOP failed")
            except (TimeoutError, OSError):
                _LOGGER.error("C-Gate keepalive connection lost")
                self._connected = False
                break
            except asyncio.CancelledError:
                return

    async def _scp_listener(self) -> None:
        """Listen for status change events on the SCP port."""
        if not self._scp_reader:
            return

        while self._connected:
            try:
                line = await self._scp_reader.readline()
                if not line:
                    _LOGGER.warning("C-Gate SCP connection closed")
                    break
                text = line.decode("ascii", errors="replace").strip()
                if not text:
                    continue

                _LOGGER.debug("C-Gate SCP: %s", text)
                self._handle_scp_event(text)

            except asyncio.CancelledError:
                return
            except (TimeoutError, OSError):
                _LOGGER.error("C-Gate SCP connection lost")
                break

    async def _event_listener(self) -> None:
        """Listen for events on the event port."""
        if not self._event_reader:
            return

        while self._connected:
            try:
                line = await self._event_reader.readline()
                if not line:
                    _LOGGER.warning("C-Gate event connection closed")
                    break
                text = line.decode("ascii", errors="replace").strip()
                if not text:
                    continue
                _LOGGER.debug("C-Gate EVT: %s", text)
            except asyncio.CancelledError:
                return
            except (TimeoutError, OSError):
                _LOGGER.error("C-Gate event connection lost")
                break

    def _handle_scp_event(self, text: str) -> None:
        """Parse and handle a status change event."""
        # Try lighting events first
        match = SCP_LIGHTING_PATTERN.match(text)
        if match:
            self._handle_lighting_event(match)
            return

        # Try measurement events
        meas_match = SCP_MEASUREMENT_PATTERN.match(text)
        if meas_match:
            self._handle_measurement_event(meas_match)

    def _handle_lighting_event(self, match: re.Match[str]) -> None:
        """Handle a lighting SCP event."""
        action = match.group(1)
        network = int(match.group(3))
        application = int(match.group(4))
        group_addr = int(match.group(5))
        level_str = match.group(6)

        group = self._get_or_create_group(network, application, group_addr)

        if action == "on":
            group.level = 255
        elif action == "off":
            group.level = 0
        elif action == "ramp" and level_str:
            # C-Gate SCP ramp reports level as 0-255 native C-Bus value
            group.level = int(level_str)

        # Notify listeners
        for callback in self._status_callbacks:
            try:
                callback(group)
            except Exception:
                _LOGGER.exception("Error in status callback")

    def _handle_measurement_event(self, match: re.Match[str]) -> None:
        """Handle a measurement SCP event.

        SCP format: measurement data //PROJECT/NET/228/DEVICE/CHANNEL VALUE EXP UNITS #sourceunit=X
        """
        network = int(match.group(2))
        application = int(match.group(3))
        device = int(match.group(4))
        channel = int(match.group(5))
        raw_value = int(match.group(6))
        exponent = int(match.group(7))
        units = int(match.group(8))
        source_unit = int(match.group(9)) if match.group(9) else 0

        uid = f"{network}_{application}_{device}_{channel}"
        if uid in self._measurements:
            meas = self._measurements[uid]
            meas.raw_value = raw_value
            meas.exponent = exponent
            meas.units = units
            meas.source_unit = source_unit
        else:
            meas = CGateMeasurement(
                network=network,
                application=application,
                device=device,
                channel=channel,
                raw_value=raw_value,
                exponent=exponent,
                units=units,
                source_unit=source_unit,
            )
            self._measurements[uid] = meas

        for callback in self._measurement_callbacks:
            try:
                callback(meas)
            except Exception:
                _LOGGER.exception("Error in measurement callback")

    def _get_or_create_group(
        self, network: int, application: int, group: int
    ) -> CGateGroup:
        """Get an existing group or create a new one."""
        g = CGateGroup(network=network, application=application, group=group)
        if g.unique_id not in self._groups:
            self._groups[g.unique_id] = g
        return self._groups[g.unique_id]
