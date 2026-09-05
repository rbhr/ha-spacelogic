"""Fault-injection tests for C-Gate connection ownership and recovery."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.spacelogic_cgate import cgate
from custom_components.spacelogic_cgate.cgate import (
    CGateClient,
    CGateCommandError,
    CGateConnectionError,
    CGateGroup,
)


@pytest.fixture
async def client():
    """Use the real connect/disconnect methods and always clean up tasks."""
    instance = CGateClient("test", 20023, 20024, 20025, "HOME")
    yield instance
    await instance.disconnect()


def transport(data: bytes = b""):
    """An actual stream reader with a controllable, non-network writer."""
    reader = asyncio.StreamReader()
    if data:
        reader.feed_data(data)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    writer.get_extra_info.return_value = None
    return reader, writer


def attach(client, data: bytes = b""):
    reader, writer = transport(data)
    client._cmd_reader, client._cmd_writer = reader, writer
    client._connected = True
    return reader, writer


@pytest.mark.parametrize("failure", [OSError("write"), TimeoutError("drain")])
async def test_write_failure_invalidates_before_next_command(client, failure):
    _, writer = attach(client)
    if isinstance(failure, TimeoutError):
        writer.drain.side_effect = failure
    else:
        writer.write.side_effect = failure
    with pytest.raises(CGateConnectionError):
        await client._send_command("NOOP")
    writer.close.assert_called_once()
    assert client._reconnect_event.is_set()
    with pytest.raises(CGateConnectionError):
        await client._send_command("NOOP")
    assert writer.write.call_count == 1


async def test_cancelled_command_cannot_donate_reply(client):
    reader, writer = attach(client)
    sent = asyncio.Event()
    writer.write.side_effect = lambda _: sent.set()
    command = asyncio.create_task(client._send_command("GET 254/56/1 level"))
    await sent.wait()
    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command
    reader.feed_data(b"300 254/56/1: level=222\r\n")
    with pytest.raises(CGateConnectionError):
        await client._send_command("GET 254/56/2 level")
    assert not client.connected
    writer.close.assert_called_once()
    assert writer.write.call_count == 1


async def test_cancelled_lock_waiter_does_not_invalidate_session(client):
    _, writer = attach(client)
    async with client._cmd_lock:
        waiter = asyncio.create_task(client._send_command("NOOP"))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    assert client.connected
    writer.write.assert_not_called()
    writer.close.assert_not_called()


async def test_old_exchange_never_reads_or_invalidates_new_socket(client):
    old_reader, old_writer = attach(client)
    sent = asyncio.Event()
    old_writer.write.side_effect = lambda _: sent.set()
    task = asyncio.create_task(client._send_command("NOOP"))
    await sent.wait()
    new_reader, new_writer = attach(client, b"200 new reply\r\n")
    old_reader.feed_data(b"200 old reply\r\n")
    with pytest.raises(CGateConnectionError):
        await task
    assert client.connected
    new_writer.close.assert_not_called()
    assert await new_reader.readline() == b"200 new reply\r\n"
    old_writer.close()


@pytest.mark.parametrize("address", ["254/56/2", "//OTHER/254/56/1", "254/57/1"])
async def test_mismatched_level_preserves_state_and_invalidates(client, address):
    _, writer = attach(client, f"300 {address}: level=222\r\n".encode())
    group = CGateGroup(254, 56, 1, level=100)
    assert await client.get_level(group) == 100
    assert not client.connected
    writer.close.assert_called_once()


@pytest.mark.parametrize("address", ["254/56/1", "//HOME/254/56/1"])
async def test_valid_level_response(client, address):
    attach(client, f"300 {address}: level=0\r\n".encode())
    assert await client.try_get_level(CGateGroup(254, 56, 1, level=200)) == 0
    assert client.connected


@pytest.mark.parametrize("failure_stage", ["greeting", "scp", "event", "project"])
async def test_failed_connect_closes_partial_sockets(client, failure_stage):
    greeting = b"400 denied\r\n" if failure_stage == "greeting" else b"201 ready\r\n"
    if failure_stage == "project":
        greeting += b"401 missing project\r\n"
    sockets = [transport(greeting), transport(), transport()]
    responses = list(sockets)
    if failure_stage in ("scp", "event"):
        responses[1 if failure_stage == "scp" else 2] = OSError("dial failed")
    with (
        patch.object(cgate.asyncio, "open_connection", AsyncMock(side_effect=responses)),
        pytest.raises(CGateConnectionError),
    ):
        await client.connect()
    count = {"greeting": 1, "scp": 1, "event": 2, "project": 3}[failure_stage]
    for _, writer in sockets[:count]:
        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()
    assert client._cmd_writer is None
    assert client._supervisor_task is None
    assert not client.connected


async def test_cancelling_handshake_closes_all_sockets(client):
    sockets = [transport(b"201 ready\r\n"), transport(), transport()]
    sent = asyncio.Event()
    sockets[0][1].write.side_effect = lambda _: sent.set()
    with patch.object(cgate.asyncio, "open_connection", AsyncMock(side_effect=sockets)):
        task = asyncio.create_task(client.connect())
        await sent.wait()
        # A command issued during handshake must fail without entering its stream.
        with pytest.raises(CGateConnectionError):
            await client._send_command("ON 254/56/1")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    for _, writer in sockets:
        assert writer.close.called
        writer.wait_closed.assert_awaited_once()
    assert client._supervisor_task is None


async def test_keepalive_eof_recovers_through_real_supervisor(client, monkeypatch):
    reader, _ = attach(client)
    reader.feed_eof()
    monkeypatch.setattr(cgate, "DEFAULT_KEEPALIVE_INTERVAL", 0)
    sockets = [transport(b"201 ready\r\n200 OK\r\n200 OK\r\n200 OK\r\n"),
               transport(), transport()]
    recovered = asyncio.Event()
    client.register_connection_callback(lambda connected: recovered.set() if connected else None)
    with patch.object(cgate.asyncio, "open_connection", AsyncMock(side_effect=sockets)):
        client._keepalive_task = asyncio.create_task(client._keepalive_loop())
        client._supervisor_task = asyncio.create_task(client._supervisor())
        # The old keepalive fails first; the new one should use the usual interval.
        await client._keepalive_task
        monkeypatch.setattr(cgate, "DEFAULT_KEEPALIVE_INTERVAL", 60)
        await asyncio.wait_for(recovered.wait(), 2)
        assert client.connected
        assert not client._supervisor_task.done()
        await client.disconnect()
        await client.disconnect()
    assert client._supervisor_task is None
    for _, writer in sockets:
        assert writer.close.called


async def test_failed_listener_does_not_interrupt_teardown(client, monkeypatch):
    _, writer = attach(client)

    async def fail():
        raise RuntimeError("listener broke")

    client._keepalive_task = asyncio.create_task(fail())
    await asyncio.sleep(0)
    # A blocked wait_closed must not prevent cleanup from completing.
    async def blocked_close():
        await asyncio.Event().wait()

    writer.wait_closed.side_effect = blocked_close
    monkeypatch.setattr(cgate, "SOCKET_CLOSE_TIMEOUT", 0.01)
    await asyncio.wait_for(client.disconnect(), 1)
    assert client._cmd_writer is None
    assert client._keepalive_task is None
    writer.close.assert_called_once()
    writer.transport.abort.assert_called_once()


async def test_cancelled_teardown_is_finished_by_disconnect(client):
    _, writer = attach(client)
    closing = asyncio.Event()
    release = asyncio.Event()

    async def wait_closed():
        closing.set()
        await release.wait()

    writer.wait_closed.side_effect = wait_closed
    task = asyncio.create_task(client._close_sockets_and_listeners())
    await closing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await client.disconnect()
    assert client._cleanup_task is None
    writer.wait_closed.assert_awaited_once()


async def test_resync_publishes_success_and_preserves_failed_reads(client):
    client._connected = True
    for number in range(1, 5):
        group = CGateGroup(254, 56, number, level=200, is_virtual=number == 4)
        client.groups[group.unique_id] = group
    updates = []
    client.register_status_callback(lambda group: updates.append((group.group, group.level)))
    with patch.object(client, "_send_command", AsyncMock(side_effect=[
        "300 //HOME/254/56/1: level=0",
        CGateCommandError("temporary", code=408),
        CGateCommandError("virtual", code=401),
    ])) as command:
        await client._resync_state()
    assert command.await_count == 3
    assert updates == [(1, 0)]
    assert [group.level for group in client.groups.values()] == [0, 200, 200, 200]


async def test_resync_stops_on_second_outage(client):
    reader, _ = attach(client)
    reader.feed_eof()
    for number in (1, 2):
        group = CGateGroup(254, 56, number, level=200)
        client.groups[group.unique_id] = group
    with patch.object(client, "try_get_level", wraps=client.try_get_level) as read:
        await client._resync_state()
    assert read.await_count == 1
    assert not client.connected


async def test_retry_backoff_jitter_and_cap(client):
    waits = []
    attempts = 0

    async def connect_once():
        nonlocal attempts
        attempts += 1
        if attempts <= 5:
            raise CGateConnectionError("still down")
        client._connected = True
        client._closing = True

    async def sleep(delay):
        waits.append(delay)

    client._reconnect_event.set()
    with (
        patch.object(client, "_connect_once", connect_once),
        patch.object(cgate.random, "uniform", return_value=1.2),
        patch.object(cgate.asyncio, "sleep", sleep),
    ):
        await client._supervisor()
    assert attempts == 6
    assert waits == [18, 36, 72, 120, 120]


async def test_successful_handshake_clears_previous_failure_signal(client):
    sockets = [transport(b"201 ready\r\n200 OK\r\n200 OK\r\n200 OK\r\n"),
               transport(), transport()]
    client._reconnect_event.set()
    with patch.object(cgate.asyncio, "open_connection", AsyncMock(side_effect=sockets)):
        await client.connect()
        await asyncio.sleep(0)
        assert not client._reconnect_event.is_set()
        assert client.connected
        await client.disconnect()


async def test_command_read_timeout_closes_session(client):
    _, writer = attach(client)
    client._cmd_reader = MagicMock(readline=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(CGateConnectionError):
        await client._send_command("NOOP")
    assert not client.connected
    assert client._reconnect_event.is_set()
    writer.close.assert_called_once()


async def test_supervisor_retries_failed_handshake_then_settles(client, monkeypatch):
    bad_reader, bad_writer = transport(b"201 ready\r\n")
    bad_writer.drain.side_effect = TimeoutError()
    failed_sockets = [(bad_reader, bad_writer), transport(), transport()]
    healthy_sockets = [
        transport(b"201 ready\r\n200 OK\r\n200 OK\r\n200 OK\r\n"),
        transport(), transport(),
    ]
    monkeypatch.setattr(cgate, "RECONNECT_DELAY", 0)
    recovered = asyncio.Event()
    client.register_connection_callback(lambda connected: recovered.set() if connected else None)
    client._reconnect_event.set()
    with patch.object(cgate.asyncio, "open_connection", AsyncMock(
        side_effect=[*failed_sockets, *healthy_sockets]
    )) as dial:
        client._supervisor_task = asyncio.create_task(client._supervisor())
        await asyncio.wait_for(recovered.wait(), 2)
        # Let an erroneously retained reconnect event trigger another dial.
        for _ in range(3):
            await asyncio.sleep(0)
        assert dial.await_count == 6
        assert not client._reconnect_event.is_set()
        assert not client._supervisor_task.done()
        await client.disconnect()
    for _, writer in [*failed_sockets, *healthy_sockets]:
        assert writer.close.called
        writer.wait_closed.assert_awaited_once()


@pytest.mark.parametrize("port", ["_scp_reader", "_event_reader"])
async def test_stream_eof_notifies_connection_loss_once(client, port):
    attach(client)
    reader = asyncio.StreamReader()
    reader.feed_eof()
    setattr(client, port, reader)
    states = []
    client.register_connection_callback(states.append)
    await (client._scp_listener() if port == "_scp_reader" else client._event_listener())
    client._mark_disconnected("another observer")
    assert states == [False]
    assert client._reconnect_event.is_set()


async def test_unexpected_listener_error_signals_recovery(client):
    attach(client)

    async def fail():
        raise ValueError("oversized frame")

    task = client._scp_task = asyncio.create_task(fail())
    task.add_done_callback(client._listener_finished)
    await asyncio.gather(task, return_exceptions=True)
    assert client._reconnect_event.is_set()
    assert not client.connected


async def test_socket_close_error_does_not_skip_other_sockets(client):
    _, command_writer = attach(client)
    _, event_writer = transport()
    client._event_writer = event_writer
    command_writer.close.side_effect = OSError("close failed")
    command_writer.wait_closed.side_effect = OSError("already lost")
    await client.disconnect()
    event_writer.close.assert_called_once()
    event_writer.wait_closed.assert_awaited_once()


async def test_concurrent_disconnect_is_idempotent(client):
    _, writer = attach(client)
    await asyncio.gather(client.disconnect(), client.disconnect())
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
