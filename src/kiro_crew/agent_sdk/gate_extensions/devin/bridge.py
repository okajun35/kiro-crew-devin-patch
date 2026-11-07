"""Devin ACP adapter with a synchronous PreToolUse -> ACP permission bridge.

Standalone package data, sealed before launch. The native CLI never receives a
durable host grant. Its own permission dialogs are answered once only after the
matching hook was allowed by Crew. State belongs to one bridge process.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import signal
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from kiro_crew import platform_compat

MAX_BYTES = 8 * 1024 * 1024
DECISION_TIMEOUT = 840
START_TIMEOUT = 60
STOP_TIMEOUT = 10
PROBE_NAME = "kiro-crew-devin-gate"
VERIFIED_VERSION = "3000.12.17"
PROJECT_CONFIGS = (
    "config.json",
    "config.local.json",
    "hooks.v1.json",
    "mcp_config.json",
    "mcp_config.local.json",
    "plugins",
)
TOOL_NAMES = {
    "exec": "execute_bash",
    "read": "fs_read",
    "write": "fs_write",
    "edit": "fs_write",
    "apply_patch": "fs_write",
    "notebook_edit": "fs_write",
    "notebook_read": "fs_read",
    "grep": "grep",
    "glob": "glob",
    "webfetch": "web_fetch",
}
TOOL_KINDS = {
    "exec": "execute",
    "write_to_process": "execute",
    "kill_shell": "execute",
    "read": "read",
    "notebook_read": "read",
    "grep": "search",
    "glob": "search",
    "write": "edit",
    "edit": "edit",
    "apply_patch": "edit",
    "notebook_edit": "edit",
    "webfetch": "fetch",
}


def project_config_issue(cwd: str) -> str:
    """Native project hooks/configs cannot become a second execution channel."""
    if not cwd:
        return "Devin requires an absolute working directory"
    for start in {Path(cwd).absolute(), Path(cwd).resolve()}:
        for directory in (start, *start.parents):
            for name in PROJECT_CONFIGS:
                path = directory / ".devin" / name
                if path.exists() or path.is_symlink():
                    return "This project has native Devin configuration; use another harness or remove its .devin configuration before starting Devin in Crew."
            if (directory / ".git").exists() or (directory / ".jj").exists():
                break
    return ""


def permission_frame(payload: dict, nonce: str, request_id: str) -> dict:
    """Translate the native hook's complete input into Crew's existing envelope."""
    name = payload["tool_name"]
    tool = TOOL_NAMES.get(name, name)
    tool_id = payload["tool_use_id"]
    arguments = payload.get("tool_input", {})
    envelope = {
        "kiro-crew-gate": 1,
        "nonce": nonce,
        "toolCallId": tool_id,
        "tool": tool,
        "kind": TOOL_KINDS.get(name, "other"),
        "input": arguments,
        "truncated": False,
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "session/request_permission",
        "params": {
            "sessionId": payload["session_id"],
            "toolCall": {
                "toolCallId": tool_id,
                "title": tool,
                "rawInput": {"method": "confirm", "title": tool, "message": json.dumps(envelope)},
            },
            "options": [
                {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
            ],
        },
    }


class Bridge:
    def __init__(self, binary: str, config: str, hook: str, nonce: str, config_home: str) -> None:
        self.binary, self.config, self.hook = binary, config, hook
        self.nonce, self.config_home = nonce, config_home
        self.token = uuid.uuid4().hex
        self.child: asyncio.subprocess.Process | None = None
        self.pending: dict[str, tuple[str, asyncio.Future[bool]]] = {}
        self.requests: dict[Any, tuple[str, dict]] = {}
        self.sessions: set[str] = set()
        self.starts: set[str] = set()
        self.approved: set[tuple[str, str]] = set()
        self.denied: set[tuple[str, str]] = set()
        self.workspaces: dict[str, str] = {}
        self.active: set[str] = set()
        self.input: asyncio.Queue[dict | None] = asyncio.Queue()
        self.output: list[dict] | None = None

    def emit(self, frame: dict) -> None:
        if self.output is not None:
            self.output.append(frame)
        else:
            sys.stdout.write(json.dumps(frame, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    async def send_child(self, frame: dict) -> None:
        assert self.child is not None and self.child.stdin is not None
        self.child.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))
        await self.child.stdin.drain()

    async def hook_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        allowed = False
        try:
            raw = await asyncio.wait_for(reader.readline(), START_TIMEOUT)
            if len(raw) > MAX_BYTES:
                raise ValueError("oversized hook")
            message = json.loads(raw)
            if not hmac.compare_digest(str(message.get("token", "")), self.token):
                raise ValueError("invalid hook token")
            if message.get("module") != self.hook:
                raise ValueError("wrong hook module")
            payload = message["payload"]
            sid = payload.get("session_id")
            if not isinstance(sid, str) or not sid:
                raise ValueError("missing session")
            event = payload.get("hook_event_name")
            if event == "SessionStart":
                self.starts.add(sid)
                allowed = True
            elif event == "PreToolUse" and sid in self.sessions:
                tool_id, name = payload.get("tool_use_id"), payload.get("tool_name")
                if (
                    not isinstance(tool_id, str)
                    or not tool_id
                    or not isinstance(name, str)
                    or not name
                ):
                    raise ValueError("missing tool identity")
                if project_config_issue(self.workspaces[sid]):
                    raise ValueError("project configuration changed")
                request_id = "crew-devin-gate-" + uuid.uuid4().hex
                future = asyncio.get_running_loop().create_future()
                self.pending[request_id] = (sid, future)
                try:
                    self.emit(permission_frame(payload, self.nonce, request_id))
                    allowed = await asyncio.wait_for(future, DECISION_TIMEOUT)
                finally:
                    self.pending.pop(request_id, None)
                (self.approved if allowed else self.denied).add((sid, tool_id))
        except Exception:
            allowed = False
        try:
            writer.write((json.dumps({"allowed": allowed}) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def reject_pending(self, sid: str | None = None) -> None:
        for owner, future in list(self.pending.values()):
            if (sid is None or sid == owner) and not future.done():
                future.set_result(False)

    async def from_host(self, frame: dict) -> None:
        ident, method = frame.get("id"), frame.get("method")
        if method is None and ident in self.pending:
            _, future = self.pending[ident]
            outcome = frame.get("result", {}).get("outcome", {})
            allowed = (
                outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow_once"
            )
            if not future.done():
                future.set_result(allowed)
            return
        params = frame.get("params", {})
        sid = params.get("sessionId", "")
        if method in ("session/new", "session/load", "session/prompt"):
            cwd = params.get("cwd") or self.workspaces.get(sid, "")
            issue = project_config_issue(cwd)
            if issue:
                self.emit(
                    {"jsonrpc": "2.0", "id": ident, "error": {"code": -32600, "message": issue}}
                )
                return
            if method == "session/prompt":
                if sid not in self.sessions:
                    self.emit(
                        {
                            "jsonrpc": "2.0",
                            "id": ident,
                            "error": {
                                "code": -32600,
                                "message": "Devin permission hook is not verified",
                            },
                        }
                    )
                    return
                self.active.add(sid)
                self.approved = {key for key in self.approved if key[0] != sid}
                self.denied = {key for key in self.denied if key[0] != sid}
            else:
                if sid:
                    self.starts.discard(sid)
                params = {**params, "_meta": {}}
                frame = {**frame, "params": params}
        if method == "session/cancel":
            self.reject_pending(sid)
        if method and ident is not None:
            self.requests[ident] = (method, params)
        await self.send_child(frame)

    async def from_child(self, frame: dict) -> None:
        method, ident = frame.get("method"), frame.get("id")
        if method == "session/request_permission":
            params = frame.get("params", {})
            key = (params.get("sessionId"), params.get("toolCall", {}).get("toolCallId"))
            allowed = key in self.approved and key not in self.denied
            kind = "allow_once" if allowed else "reject_once"
            option = next((o for o in params.get("options", []) if o.get("kind") == kind), None)
            outcome = (
                {"outcome": "selected", "optionId": option["optionId"]}
                if option
                else {"outcome": "cancelled"}
            )
            await self.send_child({"jsonrpc": "2.0", "id": ident, "result": {"outcome": outcome}})
            return
        if not method and ident in self.requests:
            request, params = self.requests.pop(ident)
            if request in ("session/new", "session/load") and "result" in frame:
                sid = frame["result"].get("sessionId") or params.get("sessionId")
                if sid not in self.starts:
                    raise RuntimeError("Devin did not load the Crew permission hook")
                self.sessions.add(sid)
                self.workspaces[sid] = params["cwd"]
            if request == "session/prompt":
                self.active.discard(params.get("sessionId", ""))
        if method == "session/update":
            params = frame.get("params", {})
            update = params.get("update", {})
            sid, tool_id = params.get("sessionId"), update.get("toolCallId")
            if (
                sid in self.active
                and update.get("sessionUpdate") == "tool_call_update"
                and update.get("status") == "completed"
                and (update.get("_meta") or {}).get("cognition.ai/eventType")
                != "hook_execution"
            ):
                if (sid, tool_id) not in self.approved or (sid, tool_id) in self.denied:
                    raise RuntimeError("Devin completed a tool without Crew permission")
        self.emit(frame)

    def stdin_thread(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            while True:
                raw = sys.stdin.buffer.readline(MAX_BYTES + 1)
                if not raw or len(raw) > MAX_BYTES:
                    break
                frame = json.loads(raw)
                loop.call_soon_threadsafe(self.input.put_nowait, frame)
        finally:
            loop.call_soon_threadsafe(self.input.put_nowait, None)

    async def host_loop(self) -> None:
        while True:
            frame = await self.input.get()
            if frame is None:
                return
            await self.from_host(frame)

    async def child_loop(self) -> None:
        assert self.child is not None and self.child.stdout is not None
        while True:
            raw = await self.child.stdout.readline()
            if not raw:
                return
            await self.from_child(json.loads(raw))

    async def stderr_loop(self) -> None:
        assert self.child is not None and self.child.stderr is not None
        while chunk := await self.child.stderr.read(65536):
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()

    async def run(self) -> None:
        server = await asyncio.start_server(self.hook_request, "127.0.0.1", 0, limit=MAX_BYTES)
        port = server.sockets[0].getsockname()[1]
        env = dict(os.environ)
        env.setdefault("RUST_LOG", "warn")
        env.update(
            {
                "XDG_CONFIG_HOME": self.config_home,
                "APPDATA": self.config_home,
                "KIROCREW_DEVIN_GATE_ADDRESS": f"127.0.0.1:{port}",
                "KIROCREW_DEVIN_GATE_TOKEN": self.token,
            }
        )
        tasks: list[asyncio.Task] = []
        loop = asyncio.get_running_loop()
        previous_term = signal.signal(
            platform_compat.SIGTERM,
            lambda *_args: loop.call_soon_threadsafe(self.input.put_nowait, None),
        )
        try:
            version_child = await asyncio.create_subprocess_exec(
                self.binary,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            try:
                version_output, _ = await asyncio.wait_for(
                    version_child.communicate(), START_TIMEOUT
                )
            except BaseException:
                if version_child.returncode is None:
                    await platform_compat.kill_and_reap(version_child, timeout=STOP_TIMEOUT)
                raise
            version = version_output.decode("utf-8", errors="replace").split()
            if version_child.returncode != 0 or version[:2] != ["devin", VERIFIED_VERSION]:
                raise RuntimeError(
                    "This Devin release has not been verified with Crew's permission hook"
                )
            self.child = await asyncio.create_subprocess_exec(
                self.binary,
                "--config",
                self.config,
                "--permission-mode",
                "auto",
                "acp",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=MAX_BYTES,
            )
            threading.Thread(
                target=self.stdin_thread, args=(asyncio.get_running_loop(),), daemon=True
            ).start()
            tasks = [
                asyncio.create_task(self.host_loop()),
                asyncio.create_task(self.child_loop()),
                asyncio.create_task(self.stderr_loop()),
            ]
            done, _ = await asyncio.wait(tasks[:2], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self.reject_pending()
            server.close()
            await server.wait_closed()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.child is not None:
                await platform_compat.kill_and_reap(self.child, timeout=STOP_TIMEOUT)
            signal.signal(platform_compat.SIGTERM, previous_term)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("binary", "config", "hook", "nonce", "config-home"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    bridge = Bridge(args.binary, args.config, args.hook, args.nonce, args.config_home)
    try:
        asyncio.run(bridge.run())
    except Exception:
        print("Devin ACP permission bridge failed closed", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
