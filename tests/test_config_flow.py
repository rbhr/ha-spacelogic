"""Tests for the config flow, focused on the reconfigure step."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import AbortFlow

from custom_components.spacelogic_cgate.cgate import CGateConnectionError
from custom_components.spacelogic_cgate.config_flow import (
    CGateConfigFlow,
    _entry_title,
)
from custom_components.spacelogic_cgate.const import (
    CONF_COMMAND_PORT,
    CONF_EVENT_PORT,
    CONF_GROUP_OVERRIDES,
    CONF_PROJECT_NAME,
    CONF_STATUS_CHANGE_PORT,
    GROUP_TYPE_RELAY,
)

OLD_DATA = {
    CONF_HOST: "192.168.1.10",
    CONF_COMMAND_PORT: 20023,
    CONF_EVENT_PORT: 20024,
    CONF_STATUS_CHANGE_PORT: 20025,
    CONF_PROJECT_NAME: "TEST_PROJECT",
}
NEW_DATA = {**OLD_DATA, CONF_HOST: "192.168.9.99"}


def _make_entry(
    data: dict[str, Any] | None = None,
    title: str | None = None,
    entry_id: str = "entry_1",
) -> MagicMock:
    """Build a stand-in config entry with just the attributes the flow reads."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = dict(data if data is not None else OLD_DATA)
    entry.options = {CONF_GROUP_OVERRIDES: {"254_56_1": GROUP_TYPE_RELAY}}
    entry.title = (
        title
        if title is not None
        else _entry_title(entry.data[CONF_HOST], entry.data[CONF_COMMAND_PORT])
    )
    return entry


def _make_flow(entry: MagicMock, other_entries: list[MagicMock] | None = None):
    """Build a CGateConfigFlow wired to `entry` for the reconfigure step."""
    flow = CGateConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config_entries.async_update_entry = MagicMock(return_value=True)
    flow._get_reconfigure_entry = MagicMock(return_value=entry)  # type: ignore[method-assign]
    flow._async_current_entries = MagicMock(  # type: ignore[method-assign]
        return_value=[entry, *(other_entries or [])]
    )
    return flow


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _patch_client(connect: Any = None):
    """Patch the CGateClient used by the flow with a connectable stub."""
    client = MagicMock()
    client.connect = AsyncMock(side_effect=connect)
    client.disconnect = AsyncMock()
    return patch(
        "custom_components.spacelogic_cgate.config_flow.CGateClient",
        return_value=client,
    )


class TestEntryTitle:
    def test_title_format(self) -> None:
        assert _entry_title("192.168.1.10", 20023) == "C-Gate (192.168.1.10:20023)"


class TestReconfigureForm:
    """The form is prefilled from the entry so nothing has to be retyped."""

    def test_shows_form_with_current_values(self) -> None:
        entry = _make_entry()
        flow = _make_flow(entry)
        flow.async_show_form = MagicMock(return_value={"type": "form"})  # type: ignore[method-assign]

        _run(flow.async_step_reconfigure())

        kwargs = flow.async_show_form.call_args.kwargs
        assert kwargs["step_id"] == "reconfigure"
        suggested = {
            key.schema: key.description["suggested_value"]
            for key in kwargs["data_schema"].schema
        }
        assert suggested[CONF_HOST] == "192.168.1.10"
        assert suggested[CONF_PROJECT_NAME] == "TEST_PROJECT"


class TestReconfigureSuccess:
    """A successful reconfigure updates the entry in place."""

    def test_updates_data_and_keeps_entry(self) -> None:
        entry = _make_entry()
        flow = _make_flow(entry)

        with _patch_client():
            result = _run(flow.async_step_reconfigure(NEW_DATA))

        assert result["reason"] == "reconfigure_successful"
        args, kwargs = flow.hass.config_entries.async_update_entry.call_args
        assert args[0] is entry
        assert kwargs["data"][CONF_HOST] == "192.168.9.99"
        assert kwargs["data"][CONF_PROJECT_NAME] == "TEST_PROJECT"

    def test_options_are_not_touched(self) -> None:
        """Group type overrides must survive — they key the entity platforms."""
        entry = _make_entry()
        flow = _make_flow(entry)

        with _patch_client():
            _run(flow.async_step_reconfigure(NEW_DATA))

        kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
        assert "options" not in kwargs
        assert entry.options == {CONF_GROUP_OVERRIDES: {"254_56_1": GROUP_TYPE_RELAY}}

    def test_no_reload_scheduled(self) -> None:
        """The entry update listener is the single reload path."""
        entry = _make_entry()
        flow = _make_flow(entry)

        with _patch_client():
            _run(flow.async_step_reconfigure(NEW_DATA))

        flow.hass.config_entries.async_schedule_reload.assert_not_called()

    def test_auto_title_follows_new_host(self) -> None:
        entry = _make_entry()
        flow = _make_flow(entry)

        with _patch_client():
            _run(flow.async_step_reconfigure(NEW_DATA))

        kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
        assert kwargs["title"] == "C-Gate (192.168.9.99:20023)"

    def test_renamed_entry_keeps_its_title(self) -> None:
        entry = _make_entry(title="Upstairs C-Gate")
        flow = _make_flow(entry)

        with _patch_client():
            _run(flow.async_step_reconfigure(NEW_DATA))

        kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
        assert "title" not in kwargs


class TestReconfigureErrors:
    def test_unreachable_host_reshows_form(self) -> None:
        entry = _make_entry()
        flow = _make_flow(entry)
        flow.async_show_form = MagicMock(return_value={"type": "form"})  # type: ignore[method-assign]

        with _patch_client(connect=CGateConnectionError("nope")):
            _run(flow.async_step_reconfigure(NEW_DATA))

        assert flow.async_show_form.call_args.kwargs["errors"] == {
            "base": "cannot_connect"
        }
        flow.hass.config_entries.async_update_entry.assert_not_called()

    def test_unexpected_error_reshows_form(self) -> None:
        entry = _make_entry()
        flow = _make_flow(entry)
        flow.async_show_form = MagicMock(return_value={"type": "form"})  # type: ignore[method-assign]

        with _patch_client(connect=ValueError("boom")):
            _run(flow.async_step_reconfigure(NEW_DATA))

        assert flow.async_show_form.call_args.kwargs["errors"] == {"base": "unknown"}
        flow.hass.config_entries.async_update_entry.assert_not_called()

    def test_unchanged_input_is_not_a_duplicate(self) -> None:
        """Resubmitting the same details must not match the entry against itself."""
        entry = _make_entry()
        flow = _make_flow(entry)

        with _patch_client():
            result = _run(flow.async_step_reconfigure(dict(OLD_DATA)))

        assert result["reason"] == "reconfigure_successful"

    def test_collision_with_another_entry_aborts(self) -> None:
        entry = _make_entry()
        other = _make_entry(data=NEW_DATA, entry_id="entry_2")
        flow = _make_flow(entry, other_entries=[other])

        with _patch_client(), pytest.raises(AbortFlow) as err:
            _run(flow.async_step_reconfigure(NEW_DATA))

        assert err.value.reason == "already_configured"


@pytest.mark.parametrize("step", ["user", "reconfigure"])
@pytest.mark.parametrize("error", [CGateConnectionError("down"), asyncio.CancelledError()])
async def test_failed_connection_test_always_disconnects(step, error):
    flow = _make_flow(_make_entry())
    flow._async_abort_entries_match = MagicMock()
    with _patch_client(connect=error) as constructor:
        if isinstance(error, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await getattr(flow, f"async_step_{step}")(NEW_DATA)
        else:
            await getattr(flow, f"async_step_{step}")(NEW_DATA)
        constructor.return_value.disconnect.assert_awaited_once()


async def test_discovery_failure_disconnects_successful_test_connection():
    flow = _make_flow(_make_entry())
    flow._async_abort_entries_match = MagicMock()
    with _patch_client() as constructor:
        constructor.return_value.discover_lighting_groups = AsyncMock(
            side_effect=CGateConnectionError("lost during discovery")
        )
        await flow.async_step_user(NEW_DATA)
        constructor.return_value.disconnect.assert_awaited_once()
