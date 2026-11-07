#!/usr/bin/env python3
"""Opt-in live Devin ACP smoke probe; never run by the offline test suite.

Uses the operator's existing Devin login and a temporary workspace. Devin retains
its native session records. Permission requests are rejected, except the fixed
echo server under --mcp, approved once per call. This does not prove that unasked
calls cannot run and does not enable the Crew backend.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REQUEST_TIMEOUT = 90
STOP_TIMEOUT = 10
MAX_FRAME_BYTES = 8 * 1024 * 1024


class Probe:
    """One stdio connection, with bounded requests and conservative replies."""

    def __init__(self, binary: str, workspace: Path, *, allow_probe_echo: bool = False) -> None:
        self.binary = binary
        self.workspace = workspace
        self.process: asyncio.subprocess.Process | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.next_id = 0
        self.text: list[str] = []
        self.permissions = 0
        self.allow_probe_echo = allow_probe_echo
        self.echo_approvals = 0

    async def __aenter__(self) -> Probe:
        self.process = await asyncio.create_subprocess_exec(
            self.binary,
            "--permission-mode",
            "auto",
            "acp",
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
        while await self.process.stderr.read(65536):
            pass

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
        """Bound the complete exchange, including a flood of notifications."""
        return await asyncio.wait_for(self._request(method, params), REQUEST_TIMEOUT)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        self.next_id += 1
        request_id = self.next_id
        await self._send(dict(jsonrpc="2.0", id=request_id, method=method, params=params))
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                raise RuntimeError(f"Devin closed stdout during {method}")
            frame = json.loads(raw)
            update = frame.get("params", {}).get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                self.text.append(update.get("content", {}).get("text", ""))
            if frame.get("method") == "session/request_permission":
                self.permissions += 1
                options = frame.get("params", {}).get("options", [])
                reject = next((o for o in options if o.get("kind") == "reject_once"), None)
                rule = (
                    frame["params"]
                    .get("toolCall", {})
                    .get("_meta", {})
                    .get("cognition.ai/permissionRule")
                )
                allow = next((o for o in options if o.get("kind") == "allow_once"), None)
                if self.allow_probe_echo and rule == "mcp__kiro-crew-probe__echo" and allow:
                    reject = allow
                    self.echo_approvals += 1
                outcome = (
                    {"outcome": "selected", "optionId": reject["optionId"]}
                    if reject
                    else {"outcome": "cancelled"}
                )
                await self._send(dict(jsonrpc="2.0", id=frame["id"], result={"outcome": outcome}))
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
                    # Native errors can quote paths or credentials: report the code only.
                    raise RuntimeError(f"{method} failed: JSON-RPC {frame['error'].get('code')}")
                return frame["result"]

    async def initialize(self) -> dict[str, Any]:
        return await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "kiro-crew-devin-probe", "version": "1"},
                "clientCapabilities": {},
            },
        )


async def probe(binary: str, *, mcp: bool = False) -> dict[str, Any]:
    """Prove the conversation survives restarting the ACP process."""
    with tempfile.TemporaryDirectory(prefix="kiro-crew-devin-") as temporary:
        workspace = Path(temporary)
        servers = (
            [
                {
                    "name": "kiro-crew-probe",
                    "command": sys.executable,
                    "args": [str(Path(__file__).resolve()), "--mcp-probe-server"],
                    "env": [],
                }
            ]
            if mcp
            else []
        )
        async with Probe(binary, workspace, allow_probe_echo=mcp) as first:
            initialized = await first.initialize()
            created = await first.request(
                "session/new", {"cwd": str(workspace), "mcpServers": servers}
            )
            session_id = created["sessionId"]
            models: list[dict[str, Any]] = next(
                (
                    o.get("options", [])
                    for o in created.get("configOptions", [])
                    if o.get("category") == "model"
                ),
                [],
            )
            first.text.clear()
            reply = await first.request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": "Remember the token DEVIN_ACP_OK. Reply with that token. Use no tools.",
                        }
                    ],
                },
            )
            if reply.get("stopReason") != "end_turn" or "DEVIN_ACP_OK" not in "".join(first.text):
                raise RuntimeError("The first prompt did not return the expected token")
            if mcp:
                first.text.clear()
                await first.request(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [
                            {
                                "type": "text",
                                "text": "Call the kiro-crew-probe MCP echo tool once, then repeat its output. Use no other tools.",
                            }
                        ],
                    },
                )
                if not first.echo_approvals or "DEVIN_MCP_OK" not in "".join(first.text):
                    raise RuntimeError("MCP echo did not ask for permission and return its marker")
        async with Probe(binary, workspace) as second:
            await second.initialize()
            await second.request(
                "session/load",
                {"sessionId": session_id, "cwd": str(workspace), "mcpServers": servers},
            )
            second.text.clear()
            resumed = await second.request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": "Reply with the token I asked you to remember. Use no tools.",
                        }
                    ],
                },
            )
            if resumed.get("stopReason") != "end_turn" or "DEVIN_ACP_OK" not in "".join(
                second.text
            ):
                raise RuntimeError("The restarted process did not recover the conversation")
        return {
            "protocol_version": initialized.get("protocolVersion"),
            "advertised_models": len(models),
            "prompt": "passed",
            "resume_after_restart": "passed",
            "mcp_echo": "passed" if mcp else "not requested",
            "probe_echo_approvals": first.echo_approvals,
            "permission_requests_rejected": first.permissions
            + second.permissions
            - first.echo_approvals,
            "crew_backend": "selectable: this native probe is separate from the Crew permission bridge",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="devin", help="Devin executable (no shell evaluation)")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Mount a fixed echo MCP server and approve only that probe tool once",
    )
    parser.add_argument("--mcp-probe-server", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.mcp_probe_server:
        serve_echo()
    else:
        print(json.dumps(asyncio.run(probe(args.binary, mcp=args.mcp)), indent=2))


def serve_echo() -> None:
    """A fixed-response MCP server: no files, shell or arbitrary tool dispatch."""
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "kiro-crew-probe", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Return the fixed ACP probe marker",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        elif method == "tools/call" and request.get("params", {}).get("name") == "echo":
            result = {"content": [{"type": "text", "text": "DEVIN_MCP_OK"}]}
        else:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32601, "message": "Unsupported"},
                    }
                ),
                flush=True,
            )
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
