"""Launch native Devin through Crew's sealed, per-process ACP permission bridge."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import uuid
from pathlib import Path

from kiro_crew.acp import launch as launch_mod
from kiro_crew.acp.harness import pi as pi_mod
from kiro_crew.acp.harness.base import ProcessAdapter, SpawnContext, SpawnPlan
from kiro_crew.acp.transport_errors import AcpToolGateUnroutable
from kiro_crew.agent_sdk.backends import ACP_BACKEND_DEVIN
from kiro_crew.owner_only_files import mkdirs_owner_only
from kiro_crew.platform_compat import isolated_python_argv

DEVIN_GATE_EXTENSION_SHA256 = {
    "hook.py": "a7baa7801532f6ff2ae289a9c111455828739e9e521aed0ade831788de038812",
    "bridge.py": "ce1be5502dca8183ecc02f9b50b422df82983b28ff6cdda0c4acf5539bd96172",
}


def seal_devin_gate() -> tuple[str, str, str]:
    """Publish verified package code under the existing read-only gate seal."""
    root = pi_mod._pi_gate_artifact_dir()
    source = Path(__file__).resolve().parents[2] / "agent_sdk" / "gate_extensions" / "devin"
    files = []
    for name in ("hook.py", "bridge.py"):
        files.append(
            pi_mod._seal_gate_extension(
                str(source / name),
                DEVIN_GATE_EXTENSION_SHA256[name],
                artifact_dir=root,
                sealed_name=f"devin-{name[:-3]}-{os.getpid()}.py",
                stage_prefix="devin-gate-",
                label="Devin permission bridge",
            )
        )
    hook, bridge = files
    config_home = str(Path(root) / "devin-config-home")
    mkdirs_owner_only(Path(config_home) / "devin")
    document = {
        "auto_update": False,
        "read_config_from": {
            name: False
            for name in (
                "agents_standard",
                "cursor",
                "windsurf",
                "claude",
                "copilot",
                "opencode",
                "zed",
            )
        },
        "hooks": {
            event: [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": shlex.join(isolated_python_argv("-I", hook)),
                            "timeout": 960,
                        }
                    ],
                }
            ]
            for event in ("SessionStart", "PreToolUse")
        },
    }
    config = pi_mod._publish_gate_artifact(
        root,
        f"devin-config-{os.getpid()}.json",
        json.dumps(document).encode("utf-8"),
        stage_prefix="devin-config-",
    )
    return hook, bridge, config


class DevinLaunch(ProcessAdapter):
    """Each session gets a gate nonce and the ordinary Crew OS sandbox."""

    backend = ACP_BACKEND_DEVIN

    async def resolve_spawn(self, ctx: SpawnContext) -> SpawnPlan:
        session = ctx.session
        assert session is not None, "Devin is launched for one session"
        from kiro_crew.agent_sdk.gate_extensions.devin.bridge import project_config_issue

        issue = await asyncio.to_thread(project_config_issue, str(ctx.work_dir or ""))
        if issue:
            raise AcpToolGateUnroutable(issue, transient=False, user_worded=True)
        binary, _argv, label, _stderr = await launch_mod.resolve_self_served_launch(self.backend)
        hook, bridge, config = await asyncio.to_thread(seal_devin_gate)
        hidden = await launch_mod._run_preflight_bounded(
            launch_mod._sandbox_preflight, self.backend, ctx.sandbox_mode
        )
        await session._prepare_session_mcp()
        nonce = uuid.uuid4().hex
        session._pi_gate_nonce = nonce
        argv = [
            *isolated_python_argv(bridge),
            "--binary",
            binary,
            "--config",
            config,
            "--hook",
            hook,
            "--nonce",
            nonce,
            "--config-home",
            str(Path(config).parent / "devin-config-home"),
        ]
        return SpawnPlan(
            argv=argv,
            spawn_label=label + " (Crew permission bridge)",
            stderr_label=label,
            extra_hidden_dirs=hidden,
        )

    def apply_spawn_env(self, env: dict[str, str], *, spawned_binary: str | None = None) -> None:
        env.pop("DEVIN_SANDBOX", None)
        env.pop("DEVIN_PERMISSION_MODE", None)
