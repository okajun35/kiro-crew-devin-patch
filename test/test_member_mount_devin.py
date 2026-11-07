"""A devin member DM session holds the member session-control tools and the panel.

The mirror (``providers/mirrors/devin.py``) uses ``place_single_binary_array``;
these drive the real resolver. The live mounts are pinned by
``test/fixtures/acp_frames/devin/member-dispatch-live.jsonl`` and
``member-panel-mount-live.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import kiro_crew.validation  # noqa: F401 - break the legacy import cycle first
from kiro_crew import acp_tool_gate
from kiro_crew.acp.client import AcpClient
from kiro_crew.acp.types import (
    ACP_BACKEND_DEVIN,
    ACP_BACKENDS_MEMBER_DISPATCH,
    ACP_BACKENDS_MEMBER_PANEL,
    ACP_BACKENDS_SESSION_MCP_ARRAY,
)
from kiro_crew.mcp_gateway.claim import STUB_SESSION_TOKEN_ENV
from kiro_crew.members import MEMBER_DISPATCH_SERVER, MEMBER_PANEL_SERVER

MEMBER_KEY = "dashboard_member-autofix"
FIXTURES = Path(__file__).parent / "fixtures" / "acp_frames" / "devin"

#: The control-plane server the agent spec declares; the mirror includes it in the
#: array because it is not identity-bound (CONTROL_PLANE_SERVERS are not withheld
#: by ``identity_bound_crew_servers``).
CONTROL_PLANE_SERVER = "kirocrew-core"


@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    """An agent spec on disk that DECLARES the dashboard server, and the panel on."""
    import kiro_crew.agent as agent_mod
    import kiro_crew.members as members_mod
    from kiro_crew.acp import session_mcp

    d = tmp_path / "agents"
    d.mkdir()
    monkeypatch.setattr(agent_mod, "KIRO_AGENTS_DIR", d)
    monkeypatch.setattr(agent_mod, "_KIRO_MCP_JSON", tmp_path / "settings-mcp.json")
    monkeypatch.setattr(session_mcp, "ensure_agent_materialized", lambda _a: True)
    monkeypatch.setattr(session_mcp, "_mcp_registry_mode", lambda: False)
    monkeypatch.setattr(members_mod, "crew_panel_enabled", lambda: True)
    (d / "kirocrew.json").write_text(
        json.dumps(
            {
                "name": "kirocrew",
                "mcpServers": {
                    CONTROL_PLANE_SERVER: {"command": "/opt/kirocrew", "args": ["mcp-core"]},
                    MEMBER_DISPATCH_SERVER: {
                        "command": "/opt/kirocrew",
                        "args": ["mcp-dashboard"],
                    },
                },
                "tools": ["@" + CONTROL_PLANE_SERVER, "@" + MEMBER_DISPATCH_SERVER],
            }
        ),
        encoding="utf-8",
    )
    return d


def _client(tmp_path: Path, session_key: str):
    """A minimally constructed ``AcpClient``: only what the resolver reads."""
    client = object.__new__(AcpClient)
    client._acp_backend = ACP_BACKEND_DEVIN
    client._mcp_gateway_overlay = None  # shared gateway off: no broker stubs
    client._agent = "kirocrew"
    client._work_dir = tmp_path / "work"
    client._session_key = session_key
    client._channel_id = "dashboard"
    client._stub_session_token = "9" * 64
    client._claude_settings_authored = False
    client._session_mcp_cache = None
    return client


def _names(out: list[dict]) -> list[str]:
    return [e["name"] for e in out]


def _member_elements(out: list[dict]) -> list[dict]:
    return [e for e in out if e["name"] in (MEMBER_DISPATCH_SERVER, MEMBER_PANEL_SERVER)]


class TestTheDecision:
    def test_devin_holds_both_things_membership_needs(self):
        """The mount it rides, and the gate that makes the mount safe, for THIS harness."""
        assert ACP_BACKEND_DEVIN in ACP_BACKENDS_MEMBER_DISPATCH
        assert ACP_BACKEND_DEVIN in ACP_BACKENDS_MEMBER_PANEL
        assert ACP_BACKEND_DEVIN in ACP_BACKENDS_SESSION_MCP_ARRAY
        assert acp_tool_gate.is_enforced(ACP_BACKEND_DEVIN) is True

    def test_the_round_trips_the_rationale_names_are_committed(self):
        dispatch = (FIXTURES / "member-dispatch-live.jsonl").read_text(encoding="utf-8")
        assert "session/request_permission" in dispatch
        assert "mcp__kirocrew-dashboard__session_status" in dispatch
        panel = (FIXTURES / "member-panel-mount-live.jsonl").read_text(encoding="utf-8")
        assert "session/request_permission" in panel
        assert "mcp__kirocrew-panel__panel_templates" in panel


class TestDevinSessionArray:
    def test_a_member_session_carries_both_member_elements(self, agents_dir, tmp_path):
        client = _client(tmp_path, MEMBER_KEY)
        out = client._resolve_session_mcp_servers()
        # kirocrew-core is control plane, not identity-bound; kirocrew-dashboard is
        # withheld from the spec-described half (identity-bound) then re-added with
        # identity by _append_member_dispatch_server.
        names = _names(out)
        assert CONTROL_PLANE_SERVER in names
        assert names[-2:] == [MEMBER_DISPATCH_SERVER, MEMBER_PANEL_SERVER]
        # The member elements carry this session's identity; spec servers on devin
        # carry only the env the spec itself names.
        for element in _member_elements(out):
            env = {p["name"]: p["value"] for p in element["env"]}
            assert env["KIROCREW_SESSION_KEY"] == MEMBER_KEY
            assert env[STUB_SESSION_TOKEN_ENV] == client._stub_session_token
        assert client.member_dispatch_mounted is True

    def test_an_ordinary_session_carries_spec_servers_only(self, agents_dir, tmp_path):
        """The spec's control-plane server is included; member mounts are not."""
        client = _client(tmp_path, "dashboard_abc123")
        assert _names(client._resolve_session_mcp_servers()) == [CONTROL_PLANE_SERVER]
        assert client.member_dispatch_mounted is False

    def test_a_disabled_dashboard_server_withholds_only_that_mount(self, agents_dir, tmp_path):
        (tmp_path / "settings-mcp.json").write_text(
            json.dumps({"mcpServers": {MEMBER_DISPATCH_SERVER: {"disabled": True}}}),
            encoding="utf-8",
        )
        client = _client(tmp_path, MEMBER_KEY)
        assert _names(client._resolve_session_mcp_servers()) == [
            CONTROL_PLANE_SERVER,
            MEMBER_PANEL_SERVER,
        ]

    def test_a_switched_off_dashboard_tool_withholds_the_whole_mount(self, agents_dir, tmp_path):
        """The mirror has no per-tool deny channel, so the narrowed server stays off."""
        (tmp_path / "settings-mcp.json").write_text(
            json.dumps(
                {"mcpServers": {MEMBER_DISPATCH_SERVER: {"disabledTools": ["session_stop"]}}}
            ),
            encoding="utf-8",
        )
        client = _client(tmp_path, MEMBER_KEY)
        assert _names(client._resolve_session_mcp_servers()) == [
            CONTROL_PLANE_SERVER,
            MEMBER_PANEL_SERVER,
        ]

    def test_the_panel_switch_withholds_only_the_panel(self, agents_dir, tmp_path, monkeypatch):
        import kiro_crew.members as members_mod

        monkeypatch.setattr(members_mod, "crew_panel_enabled", lambda: False)
        client = _client(tmp_path, MEMBER_KEY)
        assert _names(client._resolve_session_mcp_servers()) == [
            CONTROL_PLANE_SERVER,
            MEMBER_DISPATCH_SERVER,
        ]

    def test_an_unreadable_spec_still_mounts_the_member_elements(self, agents_dir, tmp_path):
        """A spec that cannot be parsed reads as no spec; the member mounts still land."""
        (agents_dir / "kirocrew.json").write_text("{not json", encoding="utf-8")
        client = _client(tmp_path, MEMBER_KEY)
        names = _names(client._resolve_session_mcp_servers())
        assert names[-2:] == [MEMBER_DISPATCH_SERVER, MEMBER_PANEL_SERVER]
        assert client._spec_zero_tools is False
