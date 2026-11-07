"""Project Crew's spec MCP servers into Devin's standard session array."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from kiro_crew.acp.session_mcp import session_mcp_projection
from kiro_crew.agent_sdk.backends import ACP_BACKEND_DEVIN
from kiro_crew.providers.mirrors.base import (
    AgentConfigMirror,
    Concern,
    Disposition,
    Ruling,
    SessionProjection,
)
from kiro_crew.providers.mirrors.opencode import place_single_binary_array


class DevinMirror(AgentConfigMirror):
    backend = ACP_BACKEND_DEVIN

    def rulings(self) -> Mapping[Concern, Ruling]:
        withheld = {
            Concern.AUTO_APPROVE: "Crew evaluates each bridged request; no native persistent grant is sent",
            Concern.PERMISSION_MODE: "Crew's sealed PreToolUse bridge owns approval independently of native persona modes",
            Concern.MODEL_ALLOWLIST: "Devin advertises the models this account can use",
            Concern.PROMPT: "Crew delivers context in the turn rather than a native Devin spec",
            Concern.RESOURCES: "No resource field is projected; context is delivered with the turn",
            Concern.HOOKS: "Native hooks are isolated; Crew spec hooks are not projected to Devin",
            Concern.TOOLS_SETTINGS: "No native mapping is claimed for Crew per-tool settings",
        }
        return {
            Concern.MCP_SERVERS: Ruling(
                Disposition.DELIVERED,
                "The standard ACP session/new and session/load mcpServers array",
            ),
            Concern.TOOL_ALLOWLIST: Ruling(
                Disposition.TRANSLATED,
                "The shared spec projection filters which servers enter the array",
            ),
            Concern.DENIED_TOOLS: Ruling(
                Disposition.TRANSLATED,
                "A server narrowed per tool is withheld whole, including Crew's own control plane",
            ),
            Concern.MODEL: Ruling(
                Disposition.DELIVERED, "Devin's advertised model session config option"
            ),
            **{key: Ruling(Disposition.WITHHELD, reason) for key, reason in withheld.items()},
        }

    def session_params(self, agent: str | None, **kwargs: Any) -> dict[str, Any]:
        return dict(self.session_projection(agent, **kwargs).params)

    def session_projection(
        self,
        agent: str | None,
        *,
        stub_server_names: Collection[str] = (),
        stub_elements: Collection[Mapping[str, Any]] = (),
        work_dir: object = None,
        session_key: str = "",
        channel_id: str = "",
        session_token: str = "",
        **kwargs: Any,
    ) -> SessionProjection:
        projection = session_mcp_projection(
            agent,
            stub_server_names=stub_server_names,
            work_dir=work_dir,  # type: ignore[arg-type]
            # A crewmate's worker reads its own spec by its session's record, as on
            # every member-capable mirror; devin carries no worker tool limits, so
            # the honoured flags stay at their refusing defaults.
            session_key=session_key,
        )
        narrowed = frozenset(server for server, _tool in projection.disabled_tools)
        servers = place_single_binary_array(
            projection,
            label="Devin",
            unhonoured=narrowed,
            stub_elements=stub_elements,
            session_key=session_key,
            channel_id=channel_id,
            session_token=session_token,
        )
        return SessionProjection(
            params={"mcpServers": servers},
            disabled_servers=projection.disabled_servers,
            restricted_servers=narrowed,
            unhonoured_servers=narrowed,
            zero_tools=projection.zero_tools,
            derived_spec_snapshot=projection.derived_spec_snapshot,
            agent_spec=projection.agent_spec,
        )
