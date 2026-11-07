#!/usr/bin/env python3
"""Opt-in live driver for Devin's sealed Crew permission bridge.

Runs the SAME argv ``acp.harness.devin`` emits (sealed bridge -> native devin
``--permission-mode auto acp`` with the Crew hook config), minus the OS sandbox,
and records every agent->client frame. Used to capture
``test/fixtures/acp_frames/devin/`` evidence for member-backend capabilities.
Never run by the offline test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

REQUEST_TIMEOUT = 240
STOP_TIMEOUT = 10
MAX_FRAME_BYTES = 8 * 1024 * 1024


class Bridge:
    """One stdio connection to the sealed bridge process."""

    def __init__(self, argv: list[str], workspace: Path, *, approve: set[str]) -> None:
        self.argv = argv
        self.workspace = workspace
        self.approve = approve
        self.process: asyncio.subprocess.Process | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.next_id = 0
        self.frames: list[dict[str, Any]] = []
        self.text: list[str] = []
        self.permissions: list[dict[str, Any]] = []
        self.approved: list[str] = []
        self.stderr_lines: list[str] = []

    async def __aenter__(self) -> "Bridge":
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.workspace,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME_BYTES,
        )
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        return self

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            self.stderr_lines.append(line.decode("utf-8", "replace").rstrip()[:300])

    async def __aexit__(self, *_exc: object) -> None:
        assert self.process is not None
        if self.process.returncode is None:
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), STOP_TIMEOUT)
        except asyncio.TimeoutError:
            self.process.kill()
            await asyncio.wait_for(self.process.wait(), STOP_TIMEOUT)
        if self.stderr_task is not None:
            await asyncio.wait_for(self.stderr_task, STOP_TIMEOUT)

    async def _send(self, frame: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.wait_for(self._request(method, params), REQUEST_TIMEOUT)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        self.next_id += 1
        request_id = self.next_id
        await self._send(dict(jsonrpc="2.0", id=request_id, method=method, params=params))
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                raise RuntimeError(f"bridge closed stdout during {method}")
            frame = json.loads(raw)
            self.frames.append(frame)
            update = frame.get("params", {}).get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                self.text.append(update.get("content", {}).get("text", ""))
            if frame.get("method") == "session/request_permission":
                tool_call = frame.get("params", {}).get("toolCall", {})
                self.permissions.append(tool_call)
                tool = tool_call.get("title") or ""
                options = frame.get("params", {}).get("options", [])
                reject = next((o for o in options if o.get("kind") == "reject_once"), None)
                allow = next((o for o in options if o.get("kind") == "allow_once"), None)
                choice = reject
                if allow and (not self.approve or tool in self.approve):
                    choice = allow
                    self.approved.append(tool)
                outcome = (
                    {"outcome": "selected", "optionId": choice["optionId"]}
                    if choice
                    else {"outcome": "cancelled"}
                )
                await self._send(
                    dict(jsonrpc="2.0", id=frame["id"], result={"outcome": outcome})
                )
            elif "method" in frame and "id" in frame:
                await self._send(
                    dict(
                        jsonrpc="2.0",
                        id=frame["id"],
                        error={"code": -32601, "message": "Unsupported"},
                    )
                )
            elif frame.get("id") == request_id and "method" not in frame:
                if "error" in frame:
                    raise RuntimeError(
                        f"{method} failed: {frame['error'].get('code')} "
                        f"{str(frame['error'].get('message'))[:200]}"
                    )
                return frame["result"]

    async def initialize(self) -> dict[str, Any]:
        return await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "kiro-crew-devin-capture", "version": "1"},
                "clientCapabilities": {},
            },
        )


def gate_argv(binary: str) -> list[str]:
    """The SpawnPlan argv acp.harness.devin emits, minus the OS sandbox."""
    from kiro_crew.acp.harness.devin import seal_devin_gate
    from kiro_crew.platform_compat import isolated_python_argv

    hook, bridge, config = seal_devin_gate()
    return [
        *isolated_python_argv(bridge),
        "--binary",
        binary,
        "--config",
        config,
        "--hook",
        hook,
        "--nonce",
        uuid.uuid4().hex,
        "--config-home",
        str(Path(config).parent / "devin-config-home"),
    ]


def dead_port_entry(name: str, session_key: str, invocation: str) -> dict[str, Any]:
    """The element the member mount emits, with the gateway port pointed nowhere."""
    from kiro_crew.members_parts.member_sessions import _member_session_element

    entry = _member_session_element(name, invocation, session_key, "")
    assert entry is not None
    return point_dead([entry])[0]


def point_dead(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Point every element's gateway port where nothing listens.

    The servers then answer their own refusal instead of reaching a live
    gateway, the same reduction the codex member-dispatch capture used.
    """
    for entry in servers:
        for env in entry.get("env") or []:
            if env.get("name") == "KIROCREW_BOUND_PORT":
                env["value"] = "1"
    return servers


async def drive(
    binary: str,
    workspace: Path,
    servers: list[dict[str, Any]],
    prompt: str,
    *,
    approve: set[str],
    load: bool = False,
    session_id: str = "",
) -> Bridge:
    async with Bridge(gate_argv(binary), workspace, approve=approve) as bridge:
        await bridge.initialize()
        method = "session/load" if load else "session/new"
        params: dict[str, Any] = {"cwd": str(workspace), "mcpServers": servers}
        if load:
            params["sessionId"] = session_id
        created = await bridge.request(method, params)
        sid = created.get("sessionId", session_id)
        reply = await bridge.request(
            "session/prompt",
            {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
        )
        bridge.reply = reply
        return bridge


def reduce_frames(frames: list[dict[str, Any]], workspace: Path) -> list[dict[str, Any]]:
    """Deterministic host-data reductions for a committed fixture."""
    out = json.loads(json.dumps(frames))
    for frame in out:
        result = frame.get("result") or {}
        if "sessionId" in result:
            result["sessionId"] = "devin-session-1"
        for option in result.get("configOptions") or []:
            current = option.get("currentValue")
            kept = [
                o
                for o in option.get("options", [])
                if o.get("value") == current
            ]
            if kept:
                option["options"] = kept
        params = frame.get("params") or {}
        if "sessionId" in params:
            params["sessionId"] = "devin-session-1"
        update = params.get("update") or {}
        sid = update.get("sessionId")
        if sid:
            update["sessionId"] = "devin-session-1"
        if params.get("sessionId"):
            params["sessionId"] = "devin-session-1"
    text = json.dumps(out)
    text = text.replace(str(workspace), "~/work")
    text = text.replace(str(Path.home()), "~")
    return json.loads(text)


async def scenario(name: str, binary: str, workspace: Path, out: Path | None) -> dict[str, Any]:
    from kiro_crew.members_parts.member_sessions import MEMBER_DISPATCH_SERVER, MEMBER_PANEL_SERVER

    servers: list[dict[str, Any]] = []
    prompt = ""
    spec_name = ""
    if name == "member-dispatch":
        servers = [dead_port_entry(MEMBER_DISPATCH_SERVER, "dashboard_member-probe", "mcp-dashboard")]
        prompt = (
            "The kirocrew-dashboard MCP server is mounted for this session. "
            "Call its session_status tool exactly once, then report what it returned. "
            "Use no other tools."
        )
    elif name == "member-panel":
        servers = [dead_port_entry(MEMBER_PANEL_SERVER, "dashboard_member-probe", "mcp-panel")]
        prompt = (
            "The kirocrew-panel MCP server is mounted for this session. "
            "Call its panel_templates tool exactly once, then report what it returned. "
            "Use no other tools."
        )
    elif name == "spec-projection":
        spec_name = "devin-probe-member"
        servers, prompt = spec_projection_array(spec_name, workspace)
        servers = point_dead(servers)
    elif name == "worker-kit":
        servers, prompt = worker_kit_array(workspace)
        servers = point_dead(servers)
    else:
        raise SystemExit(f"unknown scenario {name}")
    bridge = await drive(binary, workspace, servers, prompt, approve=set())
    summary = {
        "sessionId": next(
            (f["result"].get("sessionId") for f in bridge.frames if f.get("result", {}).get("sessionId")),
            "",
        ),
        "frames": len(bridge.frames),
        "permission_tools": [p.get("title") for p in bridge.permissions],
        "approved": bridge.approved,
        "text_tail": "".join(bridge.text)[-400:],
        "stderr_tail": bridge.stderr_lines[-5:],
        "stopReason": (bridge.reply or {}).get("stopReason"),
    }
    if out:
        lines = [json.dumps(f) for f in reduce_frames(bridge.frames, workspace)]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary["recorded"] = str(out)
    return summary


def spec_projection_array(spec_name: str, workspace: Path) -> tuple[list[dict[str, Any]], str]:
    """The mcpServers array the devin mirror emits for a saved member spec."""
    import kiro_crew.agent as agent_mod
    from kiro_crew.providers.mirrors import mirror_for

    agents_dir = workspace / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    probe = REPO_ROOT / "scripts" / "probe_devin_acp.py"
    saved = {
        "name": spec_name,
        "description": "probe member spec",
        "tools": ["*"],
        "mcpServers": {
            "spec-probe": {
                "command": sys.executable,
                "args": [str(probe), "--mcp-probe-server"],
            }
        },
    }
    (agents_dir / f"{spec_name}.json").write_text(json.dumps(saved), encoding="utf-8")
    # User-level agents dir, pointed at the seeded copy, the way the claude test does.
    agent_mod.KIRO_AGENTS_DIR = agents_dir
    projection = mirror_for("devin").session_projection(
        spec_name, work_dir=workspace, session_key="dashboard_member-probe"
    )
    servers = list(projection.params.get("mcpServers") or [])
    prompt = (
        "An MCP server named spec-probe is mounted for this session. "
        "Call its echo tool exactly once, then repeat exactly what it returned. "
        "Use no other tools."
    )
    return servers, prompt


def worker_kit_array(workspace: Path) -> tuple[list[dict[str, Any]], str]:
    """The mcpServers array the devin mirror emits for a crewmate WORKER session.

    Seeds the two in-process records a worker session's projection reads — the
    slot registration (``note_crewmate_worker_session``) and the live execution
    record (``_LIVE_EXECUTIONS``) — against the operator's real ``takt-bot``
    crewmate binding and file. Everything else runs as shipped: the worker spec
    merge, the mirror projection, the gate bridge, the harness.
    """
    from kiro_crew import execution_context
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.execution_context import resolve_member_execution
    from kiro_crew.mate_worker import MATE_WORKER_AGENT
    from kiro_crew.members import note_crewmate_worker_session
    from kiro_crew.providers.mirrors import mirror_for

    session_key = "worker-probe"
    ctx = resolve_member_execution(KiroCrewConfig.load(), "takt-bot")
    note_crewmate_worker_session(session_key, ctx.member_id or "")
    execution_context._LIVE_EXECUTIONS[execution_context._live_key(session_key)] = ctx
    projection = mirror_for("devin").session_projection(
        MATE_WORKER_AGENT, work_dir=workspace, session_key=session_key
    )
    servers = list(projection.params.get("mcpServers") or [])
    prompt = (
        "[AGENT SYSTEM PROMPT] You are takt-bot's worker session. "
        "An MCP server named for-takt:takt-bridge is mounted for this session. "
        "Call its project_list tool exactly once, then report what it returned "
        "and end your reply with the token MATE-KIT-OK. Use no other tools."
    )
    return servers, prompt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="devin")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--record", type=Path, default=None)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="devin-capture-") as temporary:
        summary = asyncio.run(
            scenario(args.scenario, args.binary, Path(temporary), args.record)
        )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
