"""Devin's permission boundary, tested without native processes or credentials."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kiro_crew.acp._dispatch import build_permission_event
from kiro_crew.acp.harness import process_adapter_for
from kiro_crew.acp.harness.base import SpawnContext
from kiro_crew.acp.harness.devin import DEVIN_GATE_EXTENSION_SHA256, DevinLaunch
from kiro_crew.acp.types import PROVIDER_LABEL_BY_BACKEND, JsonRpcMessage
from kiro_crew.agent_sdk import backend_install
from kiro_crew.agent_sdk.backends import (
    ACP_BACKEND_DEVIN,
    ACP_BACKENDS_INTERNAL_SANDBOX,
    BASELINE_SELECTABLE_BACKENDS,
    Routing,
    model_registry_namespace,
    routing_for,
)
from kiro_crew.agent_sdk.gate_extensions.devin.bridge import (
    Bridge,
    permission_frame,
    project_config_issue,
)
from kiro_crew.agent_sdk.host_auth import declaration_for


def test_devin_is_selectable_with_its_own_models_and_an_enforced_gate():
    assert ACP_BACKEND_DEVIN in BASELINE_SELECTABLE_BACKENDS
    assert PROVIDER_LABEL_BY_BACKEND[ACP_BACKEND_DEVIN] == "devin"
    assert model_registry_namespace(ACP_BACKEND_DEVIN) == "devin"
    assert routing_for(ACP_BACKEND_DEVIN) is Routing.VERIFIED_GATE_EXTENSION
    assert ACP_BACKEND_DEVIN not in ACP_BACKENDS_INTERNAL_SANDBOX


def test_gate_bytes_are_pinned_across_checkout_line_endings():
    root = Path(__file__).resolve().parents[1] / "src/kiro_crew/agent_sdk/gate_extensions/devin"
    for name, expected in DEVIN_GATE_EXTENSION_SHA256.items():
        raw = (root / name).read_bytes()
        for variant in (raw, raw.replace(b"\n", b"\r\n")):
            assert hashlib.sha256(variant.replace(b"\r\n", b"\n")).hexdigest() == expected


@pytest.mark.asyncio
async def test_launch_uses_the_sealed_bridge_and_retains_the_os_mask(tmp_path):
    session = SimpleNamespace(_prepare_session_mcp=AsyncMock(), _pi_gate_nonce="")
    ctx = SpawnContext(
        agent="probe", work_dir=tmp_path, model="auto", environ={}, home=tmp_path, session=session
    )
    adapter = process_adapter_for(ACP_BACKEND_DEVIN)
    assert isinstance(adapter, DevinLaunch)
    with (
        patch(
            "kiro_crew.acp.harness.devin.seal_devin_gate",
            return_value=("/sealed/hook.py", "/sealed/bridge.py", "/sealed/config.json"),
        ),
        patch(
            "kiro_crew.acp.launch.resolve_self_served_launch",
            new=AsyncMock(return_value=("/bin/devin", [], "devin acp", "devin")),
        ),
        patch(
            "kiro_crew.acp.launch._run_preflight_bounded",
            new=AsyncMock(return_value=("/hidden/credentials",)),
        ),
    ):
        plan = await adapter.resolve_spawn(ctx)
    assert "/sealed/bridge.py" in plan.argv
    assert plan.extra_hidden_dirs == ("/hidden/credentials",)
    assert session._pi_gate_nonce
    session._prepare_session_mcp.assert_awaited_once()


def test_install_and_auth_are_distinct_and_only_devin_owns_its_credential():
    with patch.object(backend_install.acp_driver, "self_served_resolves", return_value=False):
        state = backend_install._probe_self_served(ACP_BACKEND_DEVIN)
    assert state.installed == backend_install.MISSING
    assert state.missing_components == ("devin",)
    auth = declaration_for(ACP_BACKEND_DEVIN)
    assert "devin auth login" in auth.sign_in_remedy
    assert auth.adapter_own_leaves == (".local/share/devin/credentials.toml",)
    assert not auth.host_logout_retires_children


def test_complete_hook_input_reaches_the_real_crew_permission_parser():
    payload = {
        "session_id": "s",
        "tool_name": "exec",
        "tool_use_id": "call",
        "tool_input": {"command": "printf checked"},
    }
    frame = permission_frame(payload, "nonce", "request")
    message = JsonRpcMessage(id=frame["id"], method=frame["method"], params=frame["params"])
    event, options = build_permission_event(
        message, gate_envelope_nonce="nonce", harness_backend=ACP_BACKEND_DEVIN
    )
    assert event is not None
    assert event.is_shell
    assert event.title == "execute_bash"
    assert "printf checked" in event.tool_input
    assert options == {"once": "allow_once", "always": "allow_once", "reject": "reject_once"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "option,allowed",
    [
        ("allow_once", True),
        ("reject_once", False),
        ("switch_bypass", False),
        ("allow_session", False),
    ],
)
async def test_hook_waits_for_the_specific_crew_answer(tmp_path, option, allowed):
    bridge = Bridge("devin", "/sealed/config.json", "/sealed/hook.py", "nonce", "/sealed")
    bridge.sessions.add("s")
    bridge.workspaces["s"] = str(tmp_path)
    reader = asyncio.StreamReader()
    reader.feed_data(
        (
            json.dumps(
                {
                    "token": bridge.token,
                    "module": bridge.hook,
                    "payload": {
                        "hook_event_name": "PreToolUse",
                        "session_id": "s",
                        "tool_name": "exec",
                        "tool_use_id": "call",
                        "tool_input": {"command": "printf checked"},
                    },
                }
            )
            + "\n"
        ).encode()
    )
    written = []
    writer = SimpleNamespace(
        write=written.append, drain=AsyncMock(), close=lambda: None, wait_closed=AsyncMock()
    )
    emitted = asyncio.Event()
    frames = []

    def emit(frame):
        frames.append(frame)
        emitted.set()

    bridge.emit = emit
    task = asyncio.create_task(bridge.hook_request(reader, writer))
    try:
        await asyncio.wait_for(emitted.wait(), 30)
        assert not task.done()
        await bridge.from_host(
            {
                "jsonrpc": "2.0",
                "id": frames[0]["id"],
                "result": {"outcome": {"outcome": "selected", "optionId": option}},
            }
        )
        await asyncio.wait_for(task, 30)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert json.loads(b"".join(written))["allowed"] is allowed
    assert ("s", "call") in (bridge.approved if allowed else bridge.denied)


@pytest.mark.asyncio
async def test_native_persistent_and_bypass_choices_never_reach_crew():
    bridge = Bridge("devin", "config", "hook", "nonce", "home")
    bridge.send_child = AsyncMock()
    bridge.approved.add(("s", "call"))
    await bridge.from_child(
        {
            "jsonrpc": "2.0",
            "id": "native-request",
            "method": "session/request_permission",
            "params": {
                "sessionId": "s",
                "toolCall": {"toolCallId": "call"},
                "options": [
                    {"kind": "allow_always", "optionId": "switch_bypass"},
                    {"kind": "allow_once", "optionId": "once"},
                ],
            },
        }
    )
    assert bridge.send_child.await_args.args[0]["result"]["outcome"]["optionId"] == "once"


@pytest.mark.asyncio
async def test_missing_hook_and_unasked_completion_fail_closed():
    bridge = Bridge("devin", "config", "hook", "nonce", "home")
    bridge.requests[1] = ("session/new", {"cwd": "/work"})
    with pytest.raises(RuntimeError, match="did not load"):
        await bridge.from_child({"id": 1, "result": {"sessionId": "s"}})
    bridge.active.add("s")
    with pytest.raises(RuntimeError, match="without Crew permission"):
        await bridge.from_child(
            {
                "method": "session/update",
                "params": {
                    "sessionId": "s",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "call",
                        "status": "completed",
                    },
                },
            }
        )


@pytest.mark.asyncio
async def test_slow_hook_execution_artifact_is_not_a_tool_completion():
    """A PreToolUse hook that takes real time (a human reading the prompt) is
    surfaced by devin as a completed pseudo tool_call with its own uuid and
    ``cognition.ai/eventType: hook_execution`` — not an unapproved completion."""
    bridge = Bridge("devin", "config", "hook", "nonce", "home")
    bridge.active.add("s")
    frames = []
    bridge.emit = frames.append
    await bridge.from_child(
        {
            "method": "session/update",
            "params": {
                "sessionId": "s",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "051d47c5-9990-4853-a2be-0125d58fe53e",
                    "title": "Ran hook: hook.py (pre_tool)",
                    "status": "completed",
                    "_meta": {"cognition.ai/eventType": "hook_execution"},
                },
            },
        }
    )
    assert frames
    # The real completion still fails closed when the call was never approved.
    with pytest.raises(RuntimeError, match="without Crew permission"):
        await bridge.from_child(
            {
                "method": "session/update",
                "params": {
                    "sessionId": "s",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "051d47c5-9990-4853-a2be-0125d58fe53e",
                        "status": "completed",
                    },
                },
            }
        )


def test_native_project_hooks_are_refused_before_a_session(tmp_path):
    (tmp_path / ".git").mkdir()
    assert not project_config_issue(str(tmp_path))
    (tmp_path / ".devin").mkdir()
    (tmp_path / ".devin/hooks.v1.json").write_text("{}", encoding="utf-8")
    assert "native Devin configuration" in project_config_issue(str(tmp_path))
