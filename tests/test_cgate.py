"""Tests for the C-Gate client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.spacelogic_cgate import cgate
from custom_components.spacelogic_cgate.cgate import (
    LEVEL_RESPONSE_PATTERN,
    RESPONSE_PATTERN,
    RESPONSE_XML_BEGIN,
    RESPONSE_XML_CONTENT,
    RESPONSE_XML_END,
    SCP_LIGHTING_PATTERN,
    SCP_MEASUREMENT_PATTERN,
    CGateClient,
    CGateCommandError,
    CGateConnectionError,
    CGateGroup,
    CGateMeasurement,
    parse_xml_groups,
)


class TestCGateGroup:
    """Tests for CGateGroup."""

    def test_address(self) -> None:
        group = CGateGroup(network=254, application=56, group=1)
        assert group.address == "254/56/1"

    def test_unique_id(self) -> None:
        group = CGateGroup(network=254, application=56, group=1)
        assert group.unique_id == "254_56_1"

    def test_default_level(self) -> None:
        group = CGateGroup(network=254, application=56, group=1)
        assert group.level == 0


class TestSCPPattern:
    """Tests for SCP event parsing."""

    def test_lighting_on(self) -> None:
        match = SCP_LIGHTING_PATTERN.match(
            "lighting on //HOME/254/56/1 #sourceunit=12"
        )
        assert match is not None
        assert match.group(1) == "on"
        assert match.group(3) == "254"
        assert match.group(4) == "56"
        assert match.group(5) == "1"

    def test_lighting_off(self) -> None:
        match = SCP_LIGHTING_PATTERN.match(
            "lighting off //HOME/254/56/3 #sourceunit=8"
        )
        assert match is not None
        assert match.group(1) == "off"
        assert match.group(5) == "3"

    def test_lighting_ramp(self) -> None:
        match = SCP_LIGHTING_PATTERN.match(
            "lighting ramp //HOME/254/56/5 128 #sourceunit=4"
        )
        assert match is not None
        assert match.group(1) == "ramp"
        assert match.group(5) == "5"
        assert match.group(6) == "128"

    def test_lighting_ramp_with_percent_suffix(self) -> None:
        match = SCP_LIGHTING_PATTERN.match(
            "lighting ramp //HOME/254/56/5 200% #sourceunit=4"
        )
        assert match is not None
        assert match.group(6) == "200"

    def test_non_lighting_ignored(self) -> None:
        match = SCP_LIGHTING_PATTERN.match(
            "trigger event //HOME/254/202/1 #sourceunit=5"
        )
        assert match is None


class TestLevelResponsePattern:
    """Tests for level response parsing."""

    def test_level_response(self) -> None:
        match = LEVEL_RESPONSE_PATTERN.match("300 //HOME/254/56/1: level=128")
        assert match is not None
        assert match.group(1) == "128"

    def test_level_zero(self) -> None:
        match = LEVEL_RESPONSE_PATTERN.match("300 //HOME/254/56/1: level=0")
        assert match is not None
        assert match.group(1) == "0"

    def test_level_max(self) -> None:
        match = LEVEL_RESPONSE_PATTERN.match("300 //HOME/254/56/1: level=255")
        assert match is not None
        assert match.group(1) == "255"


# --- XML database parsing tests ---

SAMPLE_XML = """\
<Installation>
  <Project Address="HOME" TagName="HOME">
    <Network Address="254" TagName="Network 254">
      <Application Address="56" TagName="Lighting">
        <Group Address="0" TagName="Family Room Downlights"/>
        <Group Address="1" TagName="Kitchen Pendants"/>
        <Group Address="2" TagName="Master Bedroom Main"/>
        <Group Address="10" TagName="&lt;Unused&gt;"/>
        <Group Address="255" TagName="All Lights"/>
      </Application>
      <Application Address="202" TagName="Trigger">
        <Group Address="0" TagName="Trigger 0"/>
      </Application>
    </Network>
  </Project>
</Installation>
"""

SAMPLE_XML_MULTI_NETWORK = """\
<Installation>
  <Project Address="OFFICE" TagName="OFFICE">
    <Network Address="1" TagName="Network 1">
      <Application Address="56" TagName="Lighting">
        <Group Address="5" TagName="Reception Lights"/>
      </Application>
    </Network>
    <Network Address="254" TagName="Network 254">
      <Application Address="56" TagName="Lighting">
        <Group Address="0" TagName="Hallway Downlights"/>
      </Application>
    </Network>
  </Project>
</Installation>
"""

# Real C-Gate DBGETXML format uses child elements instead of XML attributes
SAMPLE_XML_CHILD_ELEMENTS = """\
<?xml version="1.0" encoding="utf-8"?>
<Installation>
  <OID>4f4a32a0-0f00-103e-a7c2-89d655d799dc</OID>
  <DBVersion>2.3</DBVersion>
  <Version>1.0</Version>
  <Project>
    <OID>4f4b9230-0f00-103e-a7c5-89d655d799dc</OID>
    <TagName>YELMAH</TagName>
    <Address>YELMAH</Address>
    <Network>
      <OID>4f4fb0e0-0f00-103e-a7fa-89d655d799dc</OID>
      <TagName>Yelmah Wired</TagName>
      <Address>254</Address>
      <Application>
        <OID>abc123</OID>
        <TagName>Lighting</TagName>
        <Address>56</Address>
        <Group>
          <OID>grp1</OID>
          <TagName>Family Room Downlights</TagName>
          <Address>0</Address>
        </Group>
        <Group>
          <OID>grp2</OID>
          <TagName>Kitchen Pendants</TagName>
          <Address>1</Address>
        </Group>
        <Group>
          <OID>grp3</OID>
          <TagName>&lt;Unused&gt;</TagName>
          <Address>10</Address>
        </Group>
        <Group>
          <OID>grp99</OID>
          <TagName>Group 99</TagName>
          <Address>99</Address>
        </Group>
        <Group>
          <OID>grp255</OID>
          <TagName>All Lights</TagName>
          <Address>255</Address>
        </Group>
      </Application>
      <Application>
        <OID>trig1</OID>
        <TagName>Trigger</TagName>
        <Address>202</Address>
        <Group>
          <OID>trg0</OID>
          <TagName>Trigger 0</TagName>
          <Address>0</Address>
        </Group>
      </Application>
    </Network>
  </Project>
</Installation>
"""


class TestParseXmlGroupsMeasurement:
    """Tests for parse_xml_groups() with measurement application."""

    def test_measurement_channels(self) -> None:
        xml = """\
<Installation>
  <Project>
    <TagName>HOME</TagName>
    <Address>HOME</Address>
    <Network>
      <Address>254</Address>
      <Application>
        <Address>228</Address>
        <TagName>Measurement</TagName>
        <Group>
          <Address>1</Address>
          <TagName>Power Meter 1</TagName>
        </Group>
        <Group>
          <Address>3</Address>
          <TagName>Temperature Sensor</TagName>
        </Group>
        <Group>
          <Address>255</Address>
          <TagName>All</TagName>
        </Group>
      </Application>
      <Application>
        <Address>56</Address>
        <TagName>Lighting</TagName>
        <Group>
          <Address>0</Address>
          <TagName>Lounge</TagName>
        </Group>
      </Application>
    </Network>
  </Project>
</Installation>"""
        channels = parse_xml_groups(xml, application=228)
        assert len(channels) == 2
        names = [c["name"] for c in channels]
        assert "Power Meter 1" in names
        assert "Temperature Sensor" in names
        # Group 255 should be filtered
        assert "All" not in names

    def test_measurement_does_not_include_lighting(self) -> None:
        channels = parse_xml_groups(SAMPLE_XML, application=228)
        assert len(channels) == 0

    def test_lighting_does_not_include_measurement(self) -> None:
        xml = """\
<Installation>
  <Project Address="HOME" TagName="HOME">
    <Network Address="254" TagName="Net">
      <Application Address="228" TagName="Measurement">
        <Group Address="1" TagName="Sensor 1"/>
      </Application>
    </Network>
  </Project>
</Installation>"""
        groups = parse_xml_groups(xml, application=56)
        assert len(groups) == 0


class TestParseXmlGroups:
    """Tests for parse_xml_groups()."""

    def test_basic_extraction(self) -> None:
        groups = parse_xml_groups(SAMPLE_XML)
        names = [g["name"] for g in groups]
        assert "Family Room Downlights" in names
        assert "Kitchen Pendants" in names
        assert "Master Bedroom Main" in names

    def test_filters_unused(self) -> None:
        groups = parse_xml_groups(SAMPLE_XML)
        names = [g["name"] for g in groups]
        assert "<Unused>" not in names

    def test_filters_group_255(self) -> None:
        groups = parse_xml_groups(SAMPLE_XML)
        addrs = [g["group"] for g in groups]
        assert 255 not in addrs

    def test_only_lighting_application(self) -> None:
        """Should only return groups from application 56 (lighting)."""
        groups = parse_xml_groups(SAMPLE_XML)
        names = [g["name"] for g in groups]
        assert "Trigger 0" not in names
        assert len(groups) == 3

    def test_network_and_application_fields(self) -> None:
        groups = parse_xml_groups(SAMPLE_XML)
        for g in groups:
            assert g["network"] == 254
            assert g["application"] == 56

    def test_multi_network(self) -> None:
        groups = parse_xml_groups(SAMPLE_XML_MULTI_NETWORK)
        assert len(groups) == 2
        networks = {g["network"] for g in groups}
        assert networks == {1, 254}
        names = {g["name"] for g in groups}
        assert names == {"Reception Lights", "Hallway Downlights"}

    def test_empty_xml(self) -> None:
        groups = parse_xml_groups("<Installation/>")
        assert groups == []

    def test_malformed_xml(self) -> None:
        groups = parse_xml_groups("this is not xml")
        assert groups == []

    def test_custom_application_filter(self) -> None:
        """Can filter for a non-lighting application."""
        groups = parse_xml_groups(SAMPLE_XML, application=202)
        assert len(groups) == 1
        assert groups[0]["name"] == "Trigger 0"

    def test_child_element_format(self) -> None:
        """Real C-Gate DBGETXML uses child elements, not XML attributes."""
        groups = parse_xml_groups(SAMPLE_XML_CHILD_ELEMENTS)
        names = [g["name"] for g in groups]
        assert "Family Room Downlights" in names
        assert "Kitchen Pendants" in names
        assert len(groups) == 2  # unused and group 255 filtered out

    def test_child_element_filters_unused(self) -> None:
        groups = parse_xml_groups(SAMPLE_XML_CHILD_ELEMENTS)
        names = [g["name"] for g in groups]
        assert "<Unused>" not in names

    def test_child_element_network_fields(self) -> None:
        groups = parse_xml_groups(SAMPLE_XML_CHILD_ELEMENTS)
        for g in groups:
            assert g["network"] == 254
            assert g["application"] == 56

    def test_child_element_filters_default_group_name(self) -> None:
        """Groups with default 'Group N' tag matching their address are skipped."""
        groups = parse_xml_groups(SAMPLE_XML_CHILD_ELEMENTS)
        names = [g["name"] for g in groups]
        assert "Group 99" not in names
        # Real named groups still present
        assert "Family Room Downlights" in names
        assert "Kitchen Pendants" in names

    def test_child_element_trigger_filter(self) -> None:
        """Can filter child-element XML for non-lighting application."""
        groups = parse_xml_groups(SAMPLE_XML_CHILD_ELEMENTS, application=202)
        assert len(groups) == 1
        assert groups[0]["name"] == "Trigger 0"


class TestDBGETXMLResponseParsing:
    """Tests for extracting XML from DBGETXML response lines."""

    def test_extract_xml_from_347_continuation_lines(self) -> None:
        """Simulate how discover_lighting_groups extracts XML from response.

        C-Gate uses continuation format: 347-content (dash after code).
        The regex captures the dash as part of group(2), so we must strip it.
        Uses real C-Gate child-element XML format.
        """
        lines = [
            "343-Begin XML snippet",
            "347-<?xml version=\"1.0\" encoding=\"utf-8\"?>",
            "347-<Installation>",
            "347-  <Project>",
            "347-    <TagName>HOME</TagName>",
            "347-    <Address>HOME</Address>",
            "347-    <Network>",
            "347-      <TagName>Net 254</TagName>",
            "347-      <Address>254</Address>",
            "347-      <Application>",
            "347-        <TagName>Lighting</TagName>",
            "347-        <Address>56</Address>",
            "347-        <Group>",
            "347-          <TagName>Lounge</TagName>",
            "347-          <Address>0</Address>",
            "347-        </Group>",
            "347-      </Application>",
            "347-    </Network>",
            "347-  </Project>",
            "347-</Installation>",
            "344 End XML snippet",
        ]

        # Same extraction logic as in discover_lighting_groups
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

        xml_text = "\n".join(xml_parts)
        groups = parse_xml_groups(xml_text)
        assert len(groups) == 1
        assert groups[0]["name"] == "Lounge"
        assert groups[0]["group"] == 0
        assert groups[0]["network"] == 254

    def test_response_code_constants(self) -> None:
        assert RESPONSE_XML_BEGIN == 343
        assert RESPONSE_XML_CONTENT == 347
        assert RESPONSE_XML_END == 344


class TestCGateClientSCPHandler:
    """Tests for SCP event handling in the client."""

    def test_handle_on_event(self, mock_cgate_client: CGateClient) -> None:
        mock_cgate_client._handle_scp_event(
            "lighting on //HOME/254/56/1 #sourceunit=12"
        )
        group = mock_cgate_client.groups.get("254_56_1")
        assert group is not None
        assert group.level == 255

    def test_handle_off_event(self, mock_cgate_client: CGateClient) -> None:
        mock_cgate_client._handle_scp_event(
            "lighting on //HOME/254/56/1 #sourceunit=12"
        )
        mock_cgate_client._handle_scp_event(
            "lighting off //HOME/254/56/1 #sourceunit=12"
        )
        group = mock_cgate_client.groups["254_56_1"]
        assert group.level == 0

    def test_handle_ramp_event_native_value(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """SCP ramp level is native 0-255 and should be stored directly."""
        mock_cgate_client._handle_scp_event(
            "lighting ramp //HOME/254/56/5 128 #sourceunit=4"
        )
        group = mock_cgate_client.groups["254_56_5"]
        assert group.level == 128

    def test_handle_ramp_full_brightness(
        self, mock_cgate_client: CGateClient
    ) -> None:
        mock_cgate_client._handle_scp_event(
            "lighting ramp //HOME/254/56/5 255 #sourceunit=4"
        )
        group = mock_cgate_client.groups["254_56_5"]
        assert group.level == 255

    def test_handle_ramp_zero(
        self, mock_cgate_client: CGateClient
    ) -> None:
        mock_cgate_client._handle_scp_event(
            "lighting ramp //HOME/254/56/5 0 #sourceunit=4"
        )
        group = mock_cgate_client.groups["254_56_5"]
        assert group.level == 0

    def test_callback_called_on_event(
        self, mock_cgate_client: CGateClient
    ) -> None:
        received: list[CGateGroup] = []
        mock_cgate_client.register_status_callback(received.append)

        mock_cgate_client._handle_scp_event(
            "lighting on //HOME/254/56/1 #sourceunit=12"
        )
        assert len(received) == 1
        assert received[0].level == 255

    def test_unsubscribe_callback(
        self, mock_cgate_client: CGateClient
    ) -> None:
        received: list[CGateGroup] = []
        unsub = mock_cgate_client.register_status_callback(received.append)

        mock_cgate_client._handle_scp_event(
            "lighting on //HOME/254/56/1 #sourceunit=12"
        )
        assert len(received) == 1

        unsub()
        mock_cgate_client._handle_scp_event(
            "lighting off //HOME/254/56/1 #sourceunit=12"
        )
        assert len(received) == 1  # no new callback


class TestAreaMatching:
    """Tests for area name matching logic."""

    def test_match_area_exact_prefix(self) -> None:
        from custom_components.spacelogic_cgate.light import _match_area

        areas = {"kitchen": "Kitchen", "family room": "Family Room"}
        assert _match_area("Kitchen Downlights", areas) == "Kitchen"

    def test_match_area_multi_word(self) -> None:
        from custom_components.spacelogic_cgate.light import _match_area

        areas = {"kitchen": "Kitchen", "family room": "Family Room"}
        assert _match_area("Family Room Pendants", areas) == "Family Room"

    def test_match_prefers_longer(self) -> None:
        from custom_components.spacelogic_cgate.light import _match_area

        areas = {
            "family": "Family",
            "family room": "Family Room",
        }
        assert _match_area("Family Room Downlights", areas) == "Family Room"

    def test_no_match(self) -> None:
        from custom_components.spacelogic_cgate.light import _match_area

        areas = {"kitchen": "Kitchen", "bedroom": "Bedroom"}
        assert _match_area("Garage Lights", areas) is None

    def test_case_insensitive(self) -> None:
        from custom_components.spacelogic_cgate.light import _match_area

        areas = {"master bedroom": "Master Bedroom"}
        assert _match_area("MASTER BEDROOM Lamps", areas) == "Master Bedroom"


class TestSCPMeasurementPattern:
    """Tests for the SCP measurement event pattern."""

    def test_measurement_data_basic(self) -> None:
        line = "measurement data //YELMAH/254/228/1/3 18500 -1 38 #sourceunit=26 OID="
        match = SCP_MEASUREMENT_PATTERN.match(line)
        assert match is not None
        assert match.group(1) == "YELMAH"  # project
        assert match.group(2) == "254"     # network
        assert match.group(3) == "228"     # application
        assert match.group(4) == "1"       # channel
        assert match.group(5) == "3"       # type
        assert match.group(6) == "18500"   # value
        assert match.group(7) == "-1"      # exponent
        assert match.group(8) == "38"      # flags
        assert match.group(9) == "26"      # sourceunit

    def test_measurement_data_negative_exponent(self) -> None:
        line = "measurement data //YELMAH/254/228/3/3 3050 -2 0 #sourceunit=25 OID="
        match = SCP_MEASUREMENT_PATTERN.match(line)
        assert match is not None
        assert match.group(6) == "3050"
        assert match.group(7) == "-2"

    def test_measurement_data_zero_value(self) -> None:
        line = "measurement data //YELMAH/254/228/4/0 0 -3 36 #sourceunit=105 OID="
        match = SCP_MEASUREMENT_PATTERN.match(line)
        assert match is not None
        assert match.group(6) == "0"

    def test_non_measurement_not_matched(self) -> None:
        line = "lighting on //HOME/254/56/1 #sourceunit=12"
        match = SCP_MEASUREMENT_PATTERN.match(line)
        assert match is None


class TestCGateMeasurement:
    """Tests for the CGateMeasurement dataclass."""

    def test_value_positive_exponent(self) -> None:
        m = CGateMeasurement(254, 228, device=1, channel=0, raw_value=5, exponent=2)
        assert m.value == 500.0

    def test_value_negative_exponent(self) -> None:
        m = CGateMeasurement(254, 228, device=1, channel=3, raw_value=18500, exponent=-1)
        assert m.value == pytest.approx(1850.0)

    def test_value_zero_exponent(self) -> None:
        m = CGateMeasurement(254, 228, device=1, channel=2, raw_value=240, exponent=0)
        assert m.value == 240.0

    def test_value_large_negative_exponent(self) -> None:
        m = CGateMeasurement(254, 228, device=3, channel=3, raw_value=3050, exponent=-2)
        assert m.value == pytest.approx(30.5)

    def test_unique_id(self) -> None:
        m = CGateMeasurement(254, 228, device=1, channel=3)
        assert m.unique_id == "254_228_1_3"

    def test_temperature_units(self) -> None:
        m = CGateMeasurement(
            254, 228, device=3, channel=1, raw_value=2350, exponent=-2,
            units=0, source_unit=25,
        )
        assert m.value == pytest.approx(23.5)
        assert m.unique_id == "254_228_3_1"


class TestMeasurementSCPHandler:
    """Tests for measurement event handling in the client."""

    def test_handle_measurement_event(
        self, mock_cgate_client: CGateClient
    ) -> None:
        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/3 18500 -1 38 #sourceunit=26 OID="
        )
        meas = mock_cgate_client.measurements.get("254_228_1_3")
        assert meas is not None
        assert meas.device == 1
        assert meas.channel == 3
        assert meas.raw_value == 18500
        assert meas.exponent == -1
        assert meas.units == 38  # Watts ($26)
        assert meas.value == pytest.approx(1850.0)
        assert meas.source_unit == 26

    def test_handle_measurement_update(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Second event for the same device/channel updates the existing object."""
        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/3 18500 -1 38 #sourceunit=26 OID="
        )
        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/3 19000 -1 38 #sourceunit=26 OID="
        )
        meas = mock_cgate_client.measurements["254_228_1_3"]
        assert meas.raw_value == 19000
        assert meas.value == pytest.approx(1900.0)

    def test_measurement_callback(
        self, mock_cgate_client: CGateClient
    ) -> None:
        received: list[CGateMeasurement] = []
        mock_cgate_client.register_measurement_callback(received.append)

        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/0 11104 -1 38 #sourceunit=26 OID="
        )
        assert len(received) == 1
        assert received[0].value == pytest.approx(1110.4)

    def test_measurement_unsubscribe(
        self, mock_cgate_client: CGateClient
    ) -> None:
        received: list[CGateMeasurement] = []
        unsub = mock_cgate_client.register_measurement_callback(received.append)

        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/0 11104 -1 38 #sourceunit=26 OID="
        )
        assert len(received) == 1

        unsub()
        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/0 12000 -1 38 #sourceunit=26 OID="
        )
        assert len(received) == 1  # no new callback

    def test_multiple_channels(
        self, mock_cgate_client: CGateClient
    ) -> None:
        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/0 11104 -1 38 #sourceunit=26 OID="
        )
        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/1/1 6131 -1 38 #sourceunit=26 OID="
        )
        mock_cgate_client._handle_scp_event(
            "measurement data //YELMAH/254/228/3/3 3050 -2 0 #sourceunit=25 OID="
        )
        assert len(mock_cgate_client.measurements) == 3
        assert "254_228_1_0" in mock_cgate_client.measurements
        assert "254_228_1_1" in mock_cgate_client.measurements
        assert "254_228_3_3" in mock_cgate_client.measurements


class TestGetLevel:
    """Tests for get_level including virtual group handling."""

    def test_get_level_success(self, mock_cgate_client: CGateClient) -> None:
        group = CGateGroup(network=254, application=56, group=1)
        mock_cgate_client._send_command = AsyncMock(return_value="300 254/56/1: level=128")
        level = asyncio.get_event_loop().run_until_complete(
            mock_cgate_client.get_level(group)
        )
        assert level == 128
        assert group.level == 128

    def test_get_level_virtual_group_returns_zero(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Virtual groups trigger a 401 error; get_level should default to 0."""
        group = CGateGroup(network=254, application=56, group=234)
        group.level = 99  # set a non-zero level to verify it gets reset
        mock_cgate_client._send_command = AsyncMock(
            side_effect=CGateCommandError(
                "C-Gate error 401: Bad object or device ID.", code=401
            )
        )
        level = asyncio.get_event_loop().run_until_complete(
            mock_cgate_client.get_level(group)
        )
        assert level == 0
        assert group.level == 0
        assert group.is_virtual is True

    def test_get_level_transient_error_keeps_last_level(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """A non-401 error must not zero the level or retire the group.

        C-Gate answers 4xx for plenty of transient reasons — mid-restart, a
        network still syncing. Folding those onto 0 switches a light off in
        Home Assistant, and flagging the group virtual takes it out of polling
        for the life of the process.
        """
        group = CGateGroup(network=254, application=56, group=1)
        group.level = 200
        mock_cgate_client._send_command = AsyncMock(
            side_effect=CGateCommandError("C-Gate error 408: Operation failed.", code=408)
        )
        level = asyncio.get_event_loop().run_until_complete(
            mock_cgate_client.get_level(group)
        )
        assert level == 200
        assert group.level == 200
        assert group.is_virtual is False

    def test_get_level_connection_error_keeps_last_level(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """An outage mid-poll leaves the last known level in place."""
        group = CGateGroup(network=254, application=56, group=1)
        group.level = 128
        mock_cgate_client._send_command = AsyncMock(
            side_effect=CGateConnectionError("Not connected to C-Gate")
        )
        level = asyncio.get_event_loop().run_until_complete(
            mock_cgate_client.get_level(group)
        )
        assert level == 128
        assert group.is_virtual is False

    def test_get_level_virtual_group_skips_command(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Once marked virtual, get_level should not call _send_command again."""
        group = CGateGroup(network=254, application=56, group=234, is_virtual=True)
        mock_cgate_client._send_command = AsyncMock()
        level = asyncio.get_event_loop().run_until_complete(
            mock_cgate_client.get_level(group)
        )
        assert level == 0
        mock_cgate_client._send_command.assert_not_called()


class TestConnectionRecovery:
    """Tests for the reconnect supervisor and what it does on the way back.

    These drive the client through _open/_teardown with those stubbed, so they
    exercise the recovery logic itself rather than asyncio's socket handling.
    """

    async def test_connect_failure_propagates(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Setup must see the first failure, so HA can retry the entry."""
        mock_cgate_client._connected = False
        mock_cgate_client._open = AsyncMock(  # type: ignore[method-assign]
            side_effect=CGateConnectionError("boom")
        )

        with pytest.raises(CGateConnectionError):
            await CGateClient.connect(mock_cgate_client)

        # No supervisor is left running behind a failed setup.
        assert mock_cgate_client._supervisor_task is None

    async def test_connect_starts_supervisor(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """A successful connect leaves a supervisor owning the connection."""
        mock_cgate_client._open = AsyncMock()  # type: ignore[method-assign]

        await CGateClient.connect(mock_cgate_client)
        try:
            assert mock_cgate_client._supervisor_task is not None
            assert not mock_cgate_client._supervisor_task.done()
        finally:
            mock_cgate_client._teardown = AsyncMock()  # type: ignore[method-assign]
            await CGateClient.disconnect(mock_cgate_client)

    async def test_connection_failed_is_idempotent(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Several components noticing one outage produce one reconnect."""
        states: list[bool] = []
        mock_cgate_client.register_connection_callback(states.append)

        mock_cgate_client._connection_failed("status port lost")
        mock_cgate_client._connection_failed("event port lost")
        mock_cgate_client._connection_failed("keepalive failed")

        assert states == [False]
        assert mock_cgate_client.connected is False
        assert mock_cgate_client._connection_lost.is_set()

    async def test_supervisor_retries_until_open_succeeds(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """A dial that keeps failing is retried, not abandoned."""
        attempts = 0
        recovered = asyncio.Event()

        async def flaky_open() -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise CGateConnectionError("still down")
            mock_cgate_client._connected = True
            recovered.set()

        mock_cgate_client._open = flaky_open  # type: ignore[method-assign]
        mock_cgate_client._teardown = AsyncMock()  # type: ignore[method-assign]
        mock_cgate_client._resync_state = AsyncMock()  # type: ignore[method-assign]

        # Collapse the backoff rather than sleeping through 2s + 4s of it.
        # Patched on the module so the supervisor picks it up by name; leaves
        # asyncio.sleep itself real, which is what lets the loop yield.
        with patch.object(cgate, "RECONNECT_INITIAL_DELAY", 0):
            supervisor = asyncio.create_task(mock_cgate_client._supervisor())
            mock_cgate_client._connection_failed("link dropped")
            await asyncio.wait_for(recovered.wait(), timeout=5)

        assert attempts == 3
        mock_cgate_client._resync_state.assert_awaited_once()

        mock_cgate_client._closing = True
        mock_cgate_client._connection_lost.set()
        await asyncio.wait_for(supervisor, timeout=5)

    async def test_supervisor_settles_after_a_handshake_that_flagged_loss(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """A reconnect must not immediately rebuild what it has just built.

        A handshake that times out reports the loss on its way out. If that
        flag survived into the attempt that finally worked, the supervisor
        would loop straight back round and tear down a healthy connection.
        """
        opens = 0
        recovered = asyncio.Event()

        async def open_once_failing() -> None:
            nonlocal opens
            opens += 1
            if opens == 1:
                mock_cgate_client._connected = True
                mock_cgate_client._connection_failed("handshake timed out")
                raise CGateConnectionError("handshake timed out")
            mock_cgate_client._connected = True
            recovered.set()

        mock_cgate_client._open = open_once_failing  # type: ignore[method-assign]
        mock_cgate_client._teardown = AsyncMock()  # type: ignore[method-assign]
        mock_cgate_client._resync_state = AsyncMock()  # type: ignore[method-assign]

        with patch.object(cgate, "RECONNECT_INITIAL_DELAY", 0):
            supervisor = asyncio.create_task(mock_cgate_client._supervisor())
            mock_cgate_client._connection_failed("link dropped")
            await asyncio.wait_for(recovered.wait(), timeout=5)
            # Give the supervisor room to loop again if it were going to.
            await asyncio.sleep(0.05)

        assert opens == 2
        assert not mock_cgate_client._connection_lost.is_set()

        mock_cgate_client._closing = True
        mock_cgate_client._connection_lost.set()
        await asyncio.wait_for(supervisor, timeout=5)

    async def test_resync_stops_when_the_link_drops_again(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """A resync interrupted by a second outage gives up promptly."""
        for group_num in (1, 2, 3):
            group = CGateGroup(network=254, application=56, group=group_num)
            mock_cgate_client._groups[group.unique_id] = group

        reads = 0

        async def drop_on_first_read(group: CGateGroup) -> int | None:
            nonlocal reads
            reads += 1
            mock_cgate_client._connected = False
            return None

        mock_cgate_client.try_get_level = drop_on_first_read  # type: ignore[method-assign]

        await mock_cgate_client._resync_state()

        # One doomed command, not one per group.
        assert reads == 1

    async def test_resync_pushes_recovered_levels(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Every group that answers is written back out to subscribers."""
        group = CGateGroup(network=254, application=56, group=1, level=0)
        mock_cgate_client._groups[group.unique_id] = group
        updates: list[CGateGroup] = []
        mock_cgate_client.register_status_callback(updates.append)
        mock_cgate_client.try_get_level = AsyncMock(  # type: ignore[method-assign]
            return_value=128
        )

        await mock_cgate_client._resync_state()

        assert group.level == 128
        assert updates == [group]

    async def test_resync_skips_groups_that_do_not_answer(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """A failed read must not be reported as level 0.

        Treating it as 0 during a partial resync switches the entity off in
        Home Assistant even though the light never changed.
        """
        group = CGateGroup(network=254, application=56, group=1, level=200)
        mock_cgate_client._groups[group.unique_id] = group
        updates: list[CGateGroup] = []
        mock_cgate_client.register_status_callback(updates.append)
        mock_cgate_client.try_get_level = AsyncMock(  # type: ignore[method-assign]
            return_value=None
        )

        await mock_cgate_client._resync_state()

        assert group.level == 200
        assert updates == []

    async def test_disconnect_stops_the_supervisor(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """Unloading the entry must not leave a task redialling forever."""
        mock_cgate_client._open = AsyncMock()  # type: ignore[method-assign]
        mock_cgate_client._teardown = AsyncMock()  # type: ignore[method-assign]

        await CGateClient.connect(mock_cgate_client)
        supervisor = mock_cgate_client._supervisor_task
        assert supervisor is not None

        await CGateClient.disconnect(mock_cgate_client)

        assert supervisor.done()
        assert mock_cgate_client._supervisor_task is None
        assert mock_cgate_client._closing is True

    async def test_connection_callback_unsubscribe(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """An entity removed from HA stops hearing about the connection."""
        states: list[bool] = []
        unsub = mock_cgate_client.register_connection_callback(states.append)

        mock_cgate_client._notify_connection_state(True)
        unsub()
        mock_cgate_client._notify_connection_state(False)

        assert states == [True]


class TestCommandPortFailures:
    """Tests for commands issued while the command port is unusable."""

    async def test_send_receive_without_connection(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """A command during an outage fails now rather than hanging."""
        mock_cgate_client._cmd_reader = None
        mock_cgate_client._cmd_writer = None

        with pytest.raises(CGateConnectionError):
            await mock_cgate_client._send_receive("NOOP")

    async def test_send_receive_eof_reports_loss(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """EOF on the command port ends the command and wakes the supervisor.

        readline() returns b"" forever once the peer has gone, so treating it
        as a blank line to skip would spin the loop hot instead.
        """
        reader = MagicMock()
        reader.readline = AsyncMock(return_value=b"")
        writer = MagicMock()
        writer.drain = AsyncMock()
        mock_cgate_client._cmd_reader = reader
        mock_cgate_client._cmd_writer = writer

        with pytest.raises(CGateConnectionError):
            await mock_cgate_client._send_receive("NOOP")

        assert mock_cgate_client.connected is False
        assert mock_cgate_client._connection_lost.is_set()

    async def test_command_error_carries_code(
        self, mock_cgate_client: CGateClient
    ) -> None:
        """The response code is what separates 401 from a transient 4xx."""
        reader = MagicMock()
        reader.readline = AsyncMock(
            return_value=b"401 Bad object or device ID.\r\n"
        )
        writer = MagicMock()
        writer.drain = AsyncMock()
        mock_cgate_client._cmd_reader = reader
        mock_cgate_client._cmd_writer = writer

        with pytest.raises(CGateCommandError) as excinfo:
            await mock_cgate_client._send_receive("GET 254/56/1 level")

        assert excinfo.value.code == 401
