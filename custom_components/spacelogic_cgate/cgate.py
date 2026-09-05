"""C-Gate TCP client for communicating with a C-Gate server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import socket
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field

from .const import (
    CBUS_LIGHTING_APPLICATION,
    CBUS_MEASUREMENT_APPLICATION,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MEASUREMENT_NETWORK,
    MEASUREMENT_SCAN_MAX_CHANNEL,
    MEASUREMENT_SCAN_MAX_DEVICE,
    RECONNECT_DELAY,
    RECONNECT_DELAY_MAX,
    RESPONSE_NO_SUCH_OBJECT,
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

# Pattern to parse a polled measurement response on the COMMAND port:
#   300 //PROJECT/NET/228/DEVICE/CHANNEL: Data=VALUE,EXPONENT,UNITS,SEQ
# Structurally different from SCP_MEASUREMENT_PATTERN above (commas after
# "Data=" rather than spaces, and no #sourceunit), so it needs its own regex.
# The 4th field's meaning is unverified; it is captured but nothing depends on it.
MEASUREMENT_DATA_RESPONSE_PATTERN = re.compile(
    r"^300\s+//(\S+?)/(\d+)/(\d+)/(\d+)/(\d+):\s*Data="
    r"(-?\d+),(-?\d+),(-?\d+)(?:,(-?\d+))?"
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
SOCKET_CLOSE_TIMEOUT = 5


class CGateConnectionError(Exception):
    """Raised when unable to connect to C-Gate server."""


class CGateCommandError(Exception):
    """Raised when a C-Gate command fails."""

    def __init__(self, message: str, code: int | None = None) -> None:
        """Keep the C-Gate response code alongside the message.

        The code is what separates a permanent failure from a transient one:
        401 means the object does not exist and never will, while a 408 from a
        C-Gate still syncing its networks clears on its own.
        """
        super().__init__(message)
        self.code = code


@dataclass
class CGateGroup:
    """Represents a C-Bus group (e.g., a lighting group)."""

    network: int
    application: int
    group: int
    level: int | None = None
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
    last_seen: float = 0.0  # time.monotonic() of the last successful reading

    @property
    def value(self) -> float:
        """Return the computed measurement value (raw_value × 10^exponent).

        Rounded by the exponent rather than left as a raw float product: the
        exponent *is* the precision, so this is exact by construction and kills
        artefacts like 23.400000000000002.
        """
        if self.exponent >= 0:
            return float(self.raw_value * 10 ** self.exponent)
        return round(self.raw_value / 10 ** -self.exponent, -self.exponent)

    @property
    def age(self) -> float:
        """Seconds since the last successful reading, or inf if never seen."""
        if not self.last_seen:
            return float("inf")
        return time.monotonic() - self.last_seen

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


def _parse_xml_project(
    xml_text: str, application: int, project_name: str | None = None
) -> tuple[list[dict[str, int | str]], set[int]]:
    """Extract groups and all network addresses, including sensor-only networks."""
    results: list[dict[str, int | str]] = []
    networks: set[int] = set()
    root = ET.fromstring(xml_text)

    for project_el in root:
        if project_el.tag.lower() != "project":
            continue
        if project_name is not None and (
            _element_value(project_el, "Address") or _element_value(project_el, "TagName")
        ) != project_name:
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
            networks.add(network_num)

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

    return results, networks


def parse_xml_groups(
    xml_text: str, application: int = CBUS_LIGHTING_APPLICATION
) -> list[dict[str, int | str]]:
    """Parse a tag database and return its named groups for an application."""
    try:
        return _parse_xml_project(xml_text, application)[0]
    except ET.ParseError:
        _LOGGER.warning("Failed to parse C-Gate XML database")
        return []


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
    _networks: set[int] = field(default_factory=set, repr=False)
    _status_callbacks: list[Callable[[CGateGroup], None]] = field(
        default_factory=list, repr=False
    )
    _measurements: dict[str, CGateMeasurement] = field(
        default_factory=dict, repr=False
    )
    _measurement_callbacks: list[Callable[[CGateMeasurement], None]] = field(
        default_factory=list, repr=False
    )
    _connection_callbacks: list[Callable[[bool], None]] = field(
        default_factory=list, repr=False
    )
    _supervisor_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _reconnect_event: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False
    )
    _closing: bool = field(default=False, repr=False)
    _disconnect_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _cleanup_task: asyncio.Task[None] | None = field(default=None, repr=False)

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

    def register_connection_callback(
        self, callback: Callable[[bool], None]
    ) -> Callable[[], None]:
        """Register a callback for connect/disconnect. Returns unsubscribe function."""
        self._connection_callbacks.append(callback)

        def unsubscribe() -> None:
            self._connection_callbacks.remove(callback)

        return unsubscribe

    def _notify_connection(self, connected: bool) -> None:
        """Tell subscribers the connection state changed."""
        for callback in tuple(self._connection_callbacks):
            try:
                callback(connected)
            except Exception:  # noqa: BLE001 - a bad subscriber must not stop the rest
                _LOGGER.exception("Error in connection callback")

    def _mark_disconnected(self, reason: str) -> None:
        """Record that the link is dead and wake the supervisor.

        Every place that notices a dead socket funnels through here. Previously
        each one simply broke out of its loop, and _scp_listener did not even
        clear _connected -- so a C-Gate restart left every entity 'available'
        with a permanently frozen value.
        """
        if self._closing:
            return
        # Close immediately, before a failed exchange releases the command lock.
        # Keep the reference so teardown can still await wait_closed().
        if self._cmd_writer is not None:
            with contextlib.suppress(OSError):
                self._cmd_writer.close()
        if self._connected:
            _LOGGER.warning("C-Gate connection lost: %s", reason)
            self._connected = False
            self._notify_connection(False)
        self._reconnect_event.set()

    def register_status_callback(
        self, callback: Callable[[CGateGroup], None]
    ) -> Callable[[], None]:
        """Register a callback for status changes. Returns unsubscribe function."""
        self._status_callbacks.append(callback)

        def unsubscribe() -> None:
            self._status_callbacks.remove(callback)

        return unsubscribe

    async def connect(self) -> None:
        """Connect, then keep the connection alive for the life of the entry."""
        self._closing = False
        await self._connect_once()
        if self._supervisor_task is None:
            self._supervisor_task = asyncio.create_task(self._supervisor())

    async def _connect_once(self) -> None:
        """Open all three sockets and start the listeners. Raises on failure."""
        # A user command must never run between PROJECT USE and PROJECT START,
        # or continue reading through a replacement of the command socket.
        try:
            async with self._cmd_lock:
                await self._open_session()
        except BaseException:
            # Includes cancellation and errors such as an invalid greeting.
            await self._close_sockets_and_listeners()
            raise

    async def _open_session(self) -> None:
        """Open a session while holding the command lock."""
        self._reconnect_event.clear()
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

            self._apply_tcp_keepalive()

            # Set up project
            await self._send_receive(f"PROJECT USE {self.project_name}", handshake=True)
            await self._send_receive(f"PROJECT START {self.project_name}", handshake=True)

            # Enable events on command session for inline monitoring
            await self._send_receive("EVENT e5s1c1", handshake=True)

            # Start background listeners
            self._connected = True
            self._scp_task = asyncio.create_task(self._scp_listener())
            self._event_task = asyncio.create_task(self._event_listener())
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            for task in (self._scp_task, self._event_task, self._keepalive_task):
                task.add_done_callback(self._listener_finished)

            self._notify_connection(True)

        except (TimeoutError, OSError, CGateCommandError) as err:
            raise CGateConnectionError(
                f"Failed to connect to C-Gate at {self.host}:{self.command_port}"
            ) from err

    def _listener_finished(self, task: asyncio.Task[None]) -> None:
        """Unexpected listener failures must also wake the supervisor."""
        if task not in (self._scp_task, self._event_task, self._keepalive_task):
            return
        if not task.cancelled() and (error := task.exception()) is not None:
            self._mark_disconnected(f"background listener failed: {error}")

    def _apply_tcp_keepalive(self) -> None:
        """Enable TCP keepalive so a silently dead socket is noticed.

        The SCP and event ports carry no guaranteed traffic -- at 3am a quiet
        house is indistinguishable from a dead socket -- so the OS has to be the
        one to notice. Linux-only options are probed rather than assumed.
        """
        for writer in (self._cmd_writer, self._scp_writer, self._event_writer):
            if writer is None:
                continue
            sock = writer.get_extra_info("socket")
            if sock is None:
                continue
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                for opt, value in (
                    ("TCP_KEEPIDLE", 60),
                    ("TCP_KEEPINTVL", 15),
                    ("TCP_KEEPCNT", 4),
                ):
                    if hasattr(socket, opt):
                        sock.setsockopt(
                            socket.IPPROTO_TCP, getattr(socket, opt), value
                        )

    async def _supervisor(self) -> None:
        """Rebuild the connection whenever it drops, until disconnect().

        Backoff is deliberately not a flat RECONNECT_DELAY: when the Clipsal CNI
        has a stale TCP slot, C-Gate itself loops open -> error -> reopen every
        15s, and a fixed 15s retry would sit in lockstep with it and fill the
        log. Only the first failure of a run is logged at WARNING.
        """
        while not self._closing:
            await self._reconnect_event.wait()
            if self._closing:
                return

            await self._close_sockets_and_listeners()

            delay = RECONNECT_DELAY
            attempt = 0
            while not self._closing:
                try:
                    await self._connect_once()
                except (CGateConnectionError, TimeoutError, OSError) as err:
                    attempt += 1
                    wait = min(delay * random.uniform(1, 1.2), RECONNECT_DELAY_MAX)
                    log = _LOGGER.warning if attempt == 1 else _LOGGER.debug
                    log(
                        "C-Gate reconnect attempt %d failed (%s); retrying in %.1fs",
                        attempt,
                        err,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    delay = min(delay * 2, RECONNECT_DELAY_MAX)
                else:
                    _LOGGER.info(
                        "C-Gate reconnected to %s:%s", self.host, self.command_port
                    )
                    await self._resync_state()
                    break

    async def _resync_state(self) -> None:
        """Recover group changes missed while the status socket was down."""
        for group in list(self._groups.values()):
            if not self._connected:
                return
            level = await self.try_get_level(group)
            if level is not None:
                group.level = level
                self._notify_status(group)

    async def disconnect(self) -> None:
        """Disconnect for good and stop the reconnect supervisor."""
        async with self._disconnect_lock:
            self._closing = True
            self._connected = False
            self._reconnect_event.set()
            supervisor, self._supervisor_task = self._supervisor_task, None
            try:
                if supervisor is not None:
                    supervisor.cancel()
                    await asyncio.gather(supervisor, return_exceptions=True)
            finally:
                await self._close_sockets_and_listeners()

    async def _close_sockets_and_listeners(self) -> None:
        """Finish teardown even if the supervisor is cancelled during cleanup."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._teardown())
        task = self._cleanup_task
        try:
            await asyncio.shield(task)
        finally:
            if task.done() and self._cleanup_task is task:
                self._cleanup_task = None

    async def _teardown(self) -> None:
        """Detach and close every resource, regardless of listener exceptions."""
        self._connected = False
        tasks = [
            task for task in (self._keepalive_task, self._scp_task, self._event_task)
            if task is not None
        ]
        writers = [
            writer for writer in (self._cmd_writer, self._scp_writer, self._event_writer)
            if writer is not None
        ]
        self._keepalive_task = None
        self._scp_task = None
        self._event_task = None

        self._cmd_writer = None
        self._scp_writer = None
        self._event_writer = None
        self._cmd_reader = None
        self._scp_reader = None
        self._event_reader = None

        for task in tasks:
            task.cancel()
        for writer in writers:
            with contextlib.suppress(OSError):
                writer.close()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                _LOGGER.warning("C-Gate listener failed during teardown: %s", result)

        async def wait_closed(writer: asyncio.StreamWriter) -> None:
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=SOCKET_CLOSE_TIMEOUT)
            except TimeoutError:
                # close() flushes buffered writes first. A peer that never reads
                # can otherwise keep the transport alive after our timeout.
                writer.transport.abort()
            except OSError:
                pass

        await asyncio.gather(*(wait_closed(writer) for writer in writers))

        _LOGGER.debug("C-Gate client disconnected")

    async def _send_receive(self, command: str, *, handshake: bool = False) -> list[str]:
        """Send a command and return all response lines.

        Must be called with _cmd_lock held.
        Raises CGateCommandError on failure responses (4xx, 5xx).
        """
        reader, writer = self._cmd_reader, self._cmd_writer
        if reader is None or writer is None or self._closing or (
            not self._connected and not handshake
        ):
            raise CGateConnectionError("Not connected to C-Gate")

        _LOGGER.debug("C-Gate TX: %s", command)
        payload = f"{command}\r\n".encode("ascii")
        try:
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), timeout=30)
            response_lines: list[str] = []
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=30)
                if writer is not self._cmd_writer or (not self._connected and not handshake):
                    raise CGateConnectionError("C-Gate session ended during command")
                if not line:
                    raise CGateConnectionError("C-Gate closed the command connection")
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
                            f"C-Gate error {code}: {match.group(2)}", code=code
                        )
                    return response_lines
        except asyncio.CancelledError:
            if writer is self._cmd_writer:
                self._mark_disconnected("command cancelled after transmission")
            raise
        except (TimeoutError, OSError, ValueError, CGateConnectionError) as err:
            if writer is self._cmd_writer:
                self._mark_disconnected(f"command exchange failed: {err}")
            raise CGateConnectionError(f"C-Gate command {command!r} failed") from err

    async def _send_command(self, command: str) -> str:
        """Send a command and return the final response line."""
        if not self._connected or self._closing:
            raise CGateConnectionError("Not connected to C-Gate")
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

    async def try_get_level(self, group: CGateGroup) -> int | None:
        """Read a level without mistaking a failed read for a successful zero."""
        if group.is_virtual:
            return None
        try:
            response = await self._send_command(f"GET {group.address} level")
        except CGateConnectionError:
            return None
        except CGateCommandError as err:
            if err.code == RESPONSE_NO_SUCH_OBJECT:
                group.is_virtual = True
            _LOGGER.debug("GET level failed for group %s: %s", group.address, err)
            return None
        match = LEVEL_RESPONSE_PATTERN.match(response)
        if match:
            address = response.split()[1].removesuffix(":")
            if address not in (group.address, f"//{self.project_name}/{group.address}"):
                # The stream is out of sync. Do not consume its remaining reply
                # as the result of the next command either.
                self._mark_disconnected("GET level response address mismatch")
                return None
            return int(match.group(1))
        return None

    async def get_level(self, group: CGateGroup) -> int | None:
        """Update a level only on success, preserving unknown and virtual state."""
        level = await self.try_get_level(group)
        if level is not None:
            group.level = level
        return group.level

    async def read_measurement(
        self, network: int, device: int, channel: int
    ) -> bool:
        """Poll one measurement channel. Returns True if a reading was stored.

        Uses a fully-qualified address so it works regardless of session state.
        A network-relative address only resolves after PROJECT USE, which cannot
        be assumed to survive an unattended reconnect -- that exact assumption is
        what silently froze the Node-RED bridge for hours on 2026-08-26.
        """
        address = (
            f"//{self.project_name}/{network}/"
            f"{CBUS_MEASUREMENT_APPLICATION}/{device}/{channel}"
        )
        response = await self._send_command(f"GET {address} Data")

        match = MEASUREMENT_DATA_RESPONSE_PATTERN.match(response)
        if not match:
            _LOGGER.debug("Unparsed measurement response for %s: %s", address, response)
            return False

        # Verify the reply is for what we asked. EVENT e5s1c1 makes C-Gate emit
        # asynchronous lines on this same session, so a mismatched address means
        # we picked up someone else's frame and must not store it as ours.
        if (
            match.group(1) != self.project_name
            or int(match.group(2)) != network
            or int(match.group(3)) != CBUS_MEASUREMENT_APPLICATION
            or int(match.group(4)) != device
            or int(match.group(5)) != channel
        ):
            _LOGGER.debug(
                "Measurement response address mismatch: asked %s, got %s",
                address,
                response,
            )
            return False

        self._update_measurement(
            network=network,
            application=int(match.group(3)),
            device=device,
            channel=channel,
            raw_value=int(match.group(6)),
            exponent=int(match.group(7)),
            units=int(match.group(8)),
        )
        return True

    async def async_refresh_measurements(
        self, channels: list[tuple[int, int, int]]
    ) -> int:
        """Poll each (network, device, channel) in turn. Returns success count.

        Sequential on purpose: every command already serialises behind
        _cmd_lock, and firing these concurrently would only queue them ahead of
        user-initiated commands such as a light switch press.
        """
        found = 0
        for network, device, channel in channels:
            try:
                if await self.read_measurement(network, device, channel):
                    found += 1
            except CGateCommandError:
                continue  # channel does not exist on this device
            except CGateConnectionError:
                raise  # link is down; supervisor handles it, stop polling
        return found

    async def scan_measurement_channels(
        self, networks: list[int] | None = None
    ) -> list[tuple[int, int, int]]:
        """Probe for measurement channels. Only for a first run; bounded."""
        if not networks:
            networks = sorted(
                self._networks or {g.network for g in self._groups.values()}
            ) or [DEFAULT_MEASUREMENT_NETWORK]

        found: list[tuple[int, int, int]] = []
        for network in networks:
            for device in range(MEASUREMENT_SCAN_MAX_DEVICE + 1):
                for channel in range(MEASUREMENT_SCAN_MAX_CHANNEL + 1):
                    try:
                        if await self.read_measurement(network, device, channel):
                            found.append((network, device, channel))
                    except CGateCommandError:
                        continue
        _LOGGER.debug("Measurement scan found %d channels", len(found))
        return found

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
            lines = await self._send_receive(f"DBGETXML //{self.project_name}")

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
            raise CGateCommandError("DBGETXML returned no XML content")

        xml_text = "\n".join(xml_parts)
        _LOGGER.debug("DBGETXML returned %d bytes of XML", len(xml_text))
        _LOGGER.debug("DBGETXML XML start: %.500s", xml_text)

        try:
            groups, self._networks = _parse_xml_project(xml_text, application, self.project_name)
        except ET.ParseError as err:
            raise CGateCommandError("DBGETXML returned invalid XML") from err
        return groups

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
            if not self.connected:
                raise CGateConnectionError("Connection lost during group discovery")
            await self.get_level(group)
        if not self.connected:
            raise CGateConnectionError("Connection lost during group discovery")

        return discovered


    async def _keepalive_loop(self) -> None:
        """Send periodic NOOP commands to keep the connection alive."""
        while self._connected:
            try:
                await asyncio.sleep(DEFAULT_KEEPALIVE_INTERVAL)
                if self._connected:
                    await self._send_command("NOOP")
            except CGateCommandError:
                _LOGGER.warning("Keepalive NOOP failed")
            except CGateConnectionError:
                # The exchange has already invalidated the socket and woken
                # the supervisor. Finish normally so teardown can await us.
                return
            except (TimeoutError, OSError) as err:
                self._mark_disconnected(f"keepalive failed: {err}")
                break
            except asyncio.CancelledError:
                return

    async def _scp_listener(self) -> None:
        """Listen for status change events on the SCP port."""
        reader = self._scp_reader
        if reader is None:
            return

        while self._connected:
            try:
                line = await reader.readline()
                if not line:
                    self._mark_disconnected("SCP connection closed")
                    break
                text = line.decode("ascii", errors="replace").strip()
                if not text:
                    continue

                _LOGGER.debug("C-Gate SCP: %s", text)
                self._handle_scp_event(text)

            except asyncio.CancelledError:
                return
            except (TimeoutError, OSError) as err:
                self._mark_disconnected(f"SCP socket error: {err}")
                break

    async def _event_listener(self) -> None:
        """Listen for events on the event port."""
        reader = self._event_reader
        if reader is None:
            return

        while self._connected:
            try:
                line = await reader.readline()
                if not line:
                    self._mark_disconnected("event connection closed")
                    break
                text = line.decode("ascii", errors="replace").strip()
                if not text:
                    continue
                _LOGGER.debug("C-Gate EVT: %s", text)
            except asyncio.CancelledError:
                return
            except (TimeoutError, OSError) as err:
                self._mark_disconnected(f"event socket error: {err}")
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
        if match.group(2) != self.project_name or int(match.group(4)) != CBUS_LIGHTING_APPLICATION:
            return
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

        self._notify_status(group)

    def _notify_status(self, group: CGateGroup) -> None:
        """Publish pushed or resynchronized group levels."""
        for callback in tuple(self._status_callbacks):
            try:
                callback(group)
            except Exception:
                _LOGGER.exception("Error in status callback")

    def _handle_measurement_event(self, match: re.Match[str]) -> None:
        """Handle a measurement SCP event.

        SCP format: measurement data //PROJECT/NET/228/DEVICE/CHANNEL VALUE EXP UNITS #sourceunit=X
        """
        if (
            match.group(1) != self.project_name
            or int(match.group(3)) != CBUS_MEASUREMENT_APPLICATION
        ):
            return
        self._update_measurement(
            network=int(match.group(2)),
            application=int(match.group(3)),
            device=int(match.group(4)),
            channel=int(match.group(5)),
            raw_value=int(match.group(6)),
            exponent=int(match.group(7)),
            units=int(match.group(8)),
            source_unit=int(match.group(9)) if match.group(9) else 0,
        )

    def _update_measurement(
        self,
        *,
        network: int,
        application: int,
        device: int,
        channel: int,
        raw_value: int,
        exponent: int,
        units: int,
        source_unit: int | None = None,
    ) -> CGateMeasurement:
        """Store a reading and notify subscribers.

        Shared by the SCP event path and the poll path. source_unit is None for
        polled readings, because the "300 ... Data=" response carries no
        #sourceunit -- it must leave any event-derived value alone rather than
        overwrite it with 0.
        """
        uid = f"{network}_{application}_{device}_{channel}"
        if uid in self._measurements:
            meas = self._measurements[uid]
            meas.raw_value = raw_value
            meas.exponent = exponent
            meas.units = units
            if source_unit is not None:
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
                source_unit=source_unit or 0,
            )
            self._measurements[uid] = meas

        meas.last_seen = time.monotonic()

        for callback in self._measurement_callbacks:
            try:
                callback(meas)
            except Exception:
                _LOGGER.exception("Error in measurement callback")

        return meas

    def _get_or_create_group(
        self, network: int, application: int, group: int
    ) -> CGateGroup:
        """Get an existing group or create a new one."""
        g = CGateGroup(network=network, application=application, group=group)
        if g.unique_id not in self._groups:
            self._groups[g.unique_id] = g
        return self._groups[g.unique_id]
