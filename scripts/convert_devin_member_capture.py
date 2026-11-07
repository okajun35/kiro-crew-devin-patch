#!/usr/bin/env python3
"""Reduce the raw drive_devin_gate.py captures into committed replay fixtures.

Writes ``test/fixtures/acp_frames/devin/<name>.jsonl`` with a ``_meta`` header
and the reduction set documented inside it. Run once per capture round; the
neighbouring ``.expected.json`` comes from
``scripts/update_acp_frame_snapshots.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "acp_frames" / "devin"

NOTES = {
    "member-dispatch-live": (
        "Recorded live on devin 3000.12.17 by scripts/drive_devin_gate.py "
        "--scenario member-dispatch, a scratch ACP client driving the real "
        "`devin --permission-mode auto acp` adapter over stdio: the same "
        "composition AcpClient performs. The session/new mcpServers array "
        "carried exactly the member dispatch element "
        "members.member_dispatch_session_server builds for a member key -- "
        "the kirocrew-dashboard stdio entry -- with KIROCREW_BOUND_PORT "
        "pointed where nothing listens, so no call reached a live gateway. "
        "The model's session_status call raised one session/request_permission "
        "through the sealed Crew gate extension (devin routing "
        "VERIFIED_GATE_EXTENSION), the consumer approved it, and the mounted "
        "server answered the call itself -- its controlled refusal "
        "(resolution_failed) because it cannot reach the gateway to read the "
        "probe key's tool policy. So the element is MOUNTED, its tool is "
        "REACHABLE, and the call is permission-gated end to end."
    ),
    "member-panel-mount-live": (
        "Recorded live on devin 3000.12.17 by scripts/drive_devin_gate.py "
        "--scenario member-panel, the same scratch-client drive as "
        "member-dispatch-live. The session/new array carried the panel "
        "element members.member_panel_session_server builds -- the "
        "kirocrew-panel stdio entry -- again with a dead KIROCREW_BOUND_PORT. "
        "The model's panel_templates call raised one "
        "session/request_permission through the gate extension, was "
        "approved, and reached the mounted server, whose own answer "
        "(resolution_failed, gateway unreachable) came back in the turn. "
        "Mounted, reachable, permission-gated."
    ),
    "worker-kit-live": (
        "Recorded live on devin 3000.12.17 by scripts/drive_devin_gate.py "
        "--scenario worker-kit. The drive registered a crewmate worker slot "
        "and live execution record for the operator's real takt-bot member "
        "binding, then asked the devin mirror for the worker session's "
        "array: session_mcp_projection resolved the per-session worker spec "
        "via mate_worker.worker_session_spec (worker base + the crewmate's "
        "own file), and the array carried the for-takt:takt-bridge server "
        "the crewmate file adds plus the managed kirocrew-core and "
        "kirocrew-cron entries. Devin's mirror withholds Concern.PROMPT, so "
        "the worker prompt rode the [AGENT SYSTEM PROMPT] context block "
        "(ACP_BACKENDS_MATE_WORKER_PROMPT_IN_CONTEXT). The model's "
        "project_list call raised one session/request_permission through "
        "the gate extension, was approved, and reached the mounted "
        "for-takt:takt-bridge child, which answered the crewmate's real "
        "project list -- the per-session spec was merged, projected, "
        "mounted and followed."
    ),
    "member-spec-projection-live": (
        "Recorded live on devin 3000.12.17 by scripts/drive_devin_gate.py "
        "--scenario spec-projection. A member-shaped agent spec "
        "(devin-probe-member, written for the drive) declared one stdio "
        "MCP server, spec-probe; session_mcp_projection carried it beside "
        "the managed kirocrew-core and kirocrew-cron entries, and the "
        "model's mcp__spec-probe__echo call raised "
        "session/request_permission through the gate extension, was "
        "approved and answered DEVIN_MCP_OK. This is the projection+mount "
        "half of a saved member spec, NOT the client-side saved-intent "
        "confirmation (member_context on AcpClient), which this drive "
        "does not exercise."
    ),
}


def reduce_frames(frames: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen_in_progress: set[str] = set()
    usage_seen = 0
    for frame in frames:
        update = (frame.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate", "")
        if kind == "available_commands_update":
            continue  # host's own command + skill catalog
        if kind == "config_option_update":
            for opt in update.get("configOptions") or []:
                if isinstance(opt, dict) and isinstance(opt.get("options"), list):
                    cur = opt.get("currentValue")
                    opt["options"] = [o for o in opt["options"] if o.get("value") == cur]
            out.append(frame)
            continue
        if kind == "usage_update":
            usage_seen += 1
            continue  # keep only the final one, re-added below
        if kind == "tool_call_update" and update.get("status") == "in_progress":
            tid = update.get("toolCallId", "")
            if tid in seen_in_progress:
                continue
            seen_in_progress.add(tid)
        out.append(frame)
    # re-append one usage frame at the end so the turn's accounting is pinned
    for frame in reversed(frames):
        if (frame.get("params") or {}).get("update", {}).get("sessionUpdate") == "usage_update":
            out.append(frame)
            break
    return out


def main() -> None:
    raw_dir = Path("/tmp")
    for name, note in NOTES.items():
        raw = raw_dir / f"devin-{name.replace('-live', '').replace('member-', 'member-').replace('worker-kit', 'worker-kit').replace('spec-projection', 'spec-projection')}.raw.jsonl"
        # explicit raw names differ from fixture names
        raw_map = {
            "member-dispatch-live": "devin-member-dispatch.raw.jsonl",
            "member-panel-mount-live": "devin-member-panel.raw.jsonl",
            "worker-kit-live": "devin-worker-kit.raw.jsonl",
            "member-spec-projection-live": "devin-spec-projection.raw.jsonl",
        }
        raw = raw_dir / raw_map[name]
        frames = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
        frames = reduce_frames(frames)
        meta = {
            "_meta": {
                "backend": "devin",
                "recorded": "live",
                "date": "2026-11-07",
                "agent_version": "3000.12.17",
                "agent_name": "devin",
                "note": note
                + " Reductions: the available_commands_update frame (the "
                "host's command and skill catalog) is dropped; every "
                "config_option_update select keeps only the option naming "
                "its currentValue; all usage_update frames but the last "
                "are dropped; a repeated in_progress tool_call_update for "
                "one toolCallId keeps only its first. The session id is "
                "the synthetic devin-session-1 the driver writes.",
            }
        }
        dest = FIXTURE_DIR / f"{name}.jsonl"
        with dest.open("w") as fh:
            fh.write(json.dumps(meta) + "\n")
            for frame in frames[:49]:
                fh.write(json.dumps(frame) + "\n")
        print(name, len(frames[:49]) + 1, "lines ->", dest)


if __name__ == "__main__":
    main()
