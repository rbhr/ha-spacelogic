"""Config flow for SpaceLogic C-Gate integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_HOST
from homeassistant.helpers import selector

from .cgate import CGateClient, CGateConnectionError, CGateGroup
from .const import (
    CONF_COMMAND_PORT,
    CONF_EVENT_PORT,
    CONF_GROUP_OVERRIDES,
    CONF_PROJECT_NAME,
    CONF_STATUS_CHANGE_PORT,
    DEFAULT_COMMAND_PORT,
    DEFAULT_EVENT_PORT,
    DEFAULT_GROUP_TYPE,
    DEFAULT_HOST,
    DEFAULT_PROJECT_NAME,
    DEFAULT_STATUS_CHANGE_PORT,
    DOMAIN,
    GROUP_TYPE_COVER,
    GROUP_TYPE_DIMMER,
    GROUP_TYPE_FAN,
    GROUP_TYPE_LOCK,
    GROUP_TYPE_RELAY,
    GROUP_TYPE_SWITCH,
    GROUP_TYPE_VALVE,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_COMMAND_PORT, default=DEFAULT_COMMAND_PORT): int,
        vol.Required(CONF_EVENT_PORT, default=DEFAULT_EVENT_PORT): int,
        vol.Required(
            CONF_STATUS_CHANGE_PORT, default=DEFAULT_STATUS_CHANGE_PORT
        ): int,
        vol.Required(CONF_PROJECT_NAME, default=DEFAULT_PROJECT_NAME): str,
    }
)

# Entity type options for the per-group selector
ENTITY_TYPE_OPTIONS = [
    selector.SelectOptionDict(value=GROUP_TYPE_COVER, label="Cover"),
    selector.SelectOptionDict(value=GROUP_TYPE_DIMMER, label="Dimmer light"),
    selector.SelectOptionDict(value=GROUP_TYPE_FAN, label="Fan"),
    selector.SelectOptionDict(value=GROUP_TYPE_LOCK, label="Lock"),
    selector.SelectOptionDict(value=GROUP_TYPE_RELAY, label="Relay light (on/off)"),
    selector.SelectOptionDict(value=GROUP_TYPE_SWITCH, label="Switch"),
    selector.SelectOptionDict(value=GROUP_TYPE_VALVE, label="Valve"),
]


def _group_display_key(group: CGateGroup) -> str:
    """Return a human-readable key for a group (used as schema field key and UI label)."""
    name = group.name or f"Group {group.group}"
    return f"{name} ({group.address})"


def _build_group_type_schema(
    groups: dict[str, CGateGroup],
    current_overrides: dict[str, str] | None = None,
) -> tuple[vol.Schema, dict[str, str]]:
    """Build a per-group entity type selector schema.

    Returns (schema, key_to_uid) where key_to_uid maps display keys back to
    group unique IDs for storage.
    """
    if current_overrides is None:
        current_overrides = {}

    sel_config = selector.SelectSelectorConfig(
        options=ENTITY_TYPE_OPTIONS,
        mode=selector.SelectSelectorMode.DROPDOWN,
    )

    fields: dict[vol.Required, selector.SelectSelector] = {}
    key_to_uid: dict[str, str] = {}
    for uid, group in sorted(
        groups.items(), key=lambda item: _group_display_key(item[1]).casefold()
    ):
        display_key = _group_display_key(group)
        key_to_uid[display_key] = uid
        current_type = current_overrides.get(uid, DEFAULT_GROUP_TYPE)
        fields[
            vol.Required(display_key, default=current_type)
        ] = selector.SelectSelector(sel_config)

    return vol.Schema(fields), key_to_uid


def _parse_group_type_input(
    user_input: dict[str, Any],
    key_to_uid: dict[str, str],
) -> dict[str, str]:
    """Convert per-group selector input into a group_overrides dict."""
    overrides: dict[str, str] = {}
    for display_key, group_type in user_input.items():
        uid = key_to_uid.get(display_key, display_key)
        if group_type != DEFAULT_GROUP_TYPE:
            overrides[uid] = group_type
    return overrides


class CGateOptionsFlow(OptionsFlowWithConfigEntry):
    """Handle options for SpaceLogic C-Gate."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        super().__init__(config_entry)
        self._key_to_uid: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage group type options via per-group dropdowns."""
        if user_input is not None:
            group_overrides = _parse_group_type_input(
                user_input, self._key_to_uid
            )
            data: dict[str, Any] = {}
            if group_overrides:
                data[CONF_GROUP_OVERRIDES] = group_overrides
            return self.async_create_entry(data=data)

        client: CGateClient | None = getattr(
            self.config_entry, "runtime_data", None
        )
        groups = client.groups if client else {}
        current_overrides: dict[str, str] = self.options.get(
            CONF_GROUP_OVERRIDES, {}
        )

        schema, self._key_to_uid = _build_group_type_schema(
            groups, current_overrides
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class CGateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SpaceLogic C-Gate."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}
        self._discovered_groups: dict[str, CGateGroup] = {}
        self._key_to_uid: dict[str, str] = {}

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CGateOptionsFlow:
        """Get the options flow handler."""
        return CGateOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — connection details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match(
                {
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_COMMAND_PORT: user_input[CONF_COMMAND_PORT],
                    CONF_PROJECT_NAME: user_input[CONF_PROJECT_NAME],
                }
            )

            client = CGateClient(
                host=user_input[CONF_HOST],
                command_port=user_input[CONF_COMMAND_PORT],
                event_port=user_input[CONF_EVENT_PORT],
                status_change_port=user_input[CONF_STATUS_CHANGE_PORT],
                project_name=user_input[CONF_PROJECT_NAME],
            )

            try:
                await client.connect()
                discovered = await client.discover_lighting_groups()
            except CGateConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during connection test")
                errors["base"] = "unknown"
            else:
                await client.disconnect()
                self._user_input = user_input
                self._discovered_groups = {
                    g.unique_id: g for g in discovered
                }
                return await self.async_step_group_types()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_group_types(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the second step — assign entity types to groups."""
        if user_input is not None:
            overrides = _parse_group_type_input(user_input, self._key_to_uid)
            return self.async_create_entry(
                title=(
                    f"C-Gate ({self._user_input[CONF_HOST]}"
                    f":{self._user_input[CONF_COMMAND_PORT]})"
                ),
                data=self._user_input,
                options={CONF_GROUP_OVERRIDES: overrides} if overrides else {},
            )

        schema, self._key_to_uid = _build_group_type_schema(
            self._discovered_groups
        )
        return self.async_show_form(
            step_id="group_types", data_schema=schema
        )
