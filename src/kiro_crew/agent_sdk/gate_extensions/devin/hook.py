"""Devin lifecycle hook: every tool waits for Crew, and transport errors block."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
SOCKET_TIMEOUT = 900


def decide(payload: dict) -> dict:
    address = os.environ["KIROCREW_DEVIN_GATE_ADDRESS"]
    host, port = address.rsplit(":", 1)
    if host != "127.0.0.1":
        raise ValueError("the gate must be local")
    request = {
        "token": os.environ["KIROCREW_DEVIN_GATE_TOKEN"],
        "module": str(Path(__file__).resolve()),
        "payload": payload,
    }
    encoded = (json.dumps(request) + "\n").encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("tool input is too large")
    with socket.create_connection((host, int(port)), timeout=SOCKET_TIMEOUT) as connection:
        connection.sendall(encoded)
        with connection.makefile("rb") as stream:
            raw = stream.readline(MAX_BYTES + 1)
        if not raw.endswith(b"\n") or len(raw) > MAX_BYTES:
            raise ValueError("invalid gate reply")
        reply = json.loads(raw)
    if reply.get("allowed") is not True:
        return {"decision": "block", "reason": "Denied by Kiro Crew"}
    return {"decision": "approve"}


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("tool input is too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("invalid hook input")
        result = decide(payload)
    except Exception:
        # Devin treats a hook exception or timeout as fail-open. Return a real
        # blocking decision instead, without exposing credentials in an error.
        result = {"decision": "block", "reason": "Kiro Crew permission channel unavailable"}
    print(json.dumps(result), flush=True)
    if result["decision"] == "block":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
