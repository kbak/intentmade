"""Resolve operator-selected OpenHands profiles; keep stage authority in the factory."""

from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import quote

import deployment
from common import api
from openhands.sdk import LLM
from openhands.sdk.profiles import resolve_agent_profile, validate_agent_profile
from pydantic import BaseModel, SecretStr

CURRENT = ContextVar("factory_agent_profile", default=None)


@dataclass
class Selection:
    settings: object
    identity: dict
    read_roots: list[str]

    @property
    def native(self):
        return self.settings.agent_kind == "openhands"


class CanvasLLMStore:
    def load(self, name, *, cipher=None):
        payload = api(
            "GET",
            "/api/profiles/" + quote(name, safe=""),
            headers={"X-Expose-Secrets": "plaintext"},
        )
        llm = LLM.model_validate(payload["config"])
        if llm.auth_type == "subscription":
            raise ValueError(
                "Native factory workers require an API-backed LLM profile; use Codex ACP for subscription login"
            )
        return llm


def credential_values(value):
    """Collect known LLM secrets for existing diagnostic redaction only."""
    if isinstance(value, SecretStr):
        return [value.get_secret_value()]
    if isinstance(value, BaseModel):
        return [
            secret
            for name in type(value).model_fields
            for secret in credential_values(getattr(value, name))
        ]
    if isinstance(value, dict):
        return [secret for item in value.values() for secret in credential_values(item)]
    if isinstance(value, (list, tuple)):
        return [secret for item in value for secret in credential_values(item)]
    return []


def profile_name():
    return deployment.settings()["worker_agent_profile"]


# [impl->req~im-agent-profile~1]
def resolve(payload, name, roots=()):
    profile = validate_agent_profile({"name": name, **payload})
    if profile.agent_kind == "acp" and profile.acp_server != "codex":
        raise ValueError("Factory workers currently support Codex ACP and native OpenHands")
    if profile.agent_kind == "acp" and profile.secret_refs is not None:
        if "CODEX_AUTH_JSON" not in profile.secret_refs:
            raise ValueError("The Codex worker profile must allow CODEX_AUTH_JSON")
    # Saved tools/MCP/secrets do not grant new stage permissions. Only explicitly
    # supplied workflow MCP servers and the selected model credential cross over.
    settings = resolve_agent_profile(
        profile.model_copy(update={"mcp_server_refs": []}),
        llm_store=CanvasLLMStore(),
        mcp_config={},
        available_skills=[],
    )
    if profile.agent_kind == "acp":
        settings.acp_command = ["codex-acp"]
        settings.acp_args = []
    identity = {
        "name": name,
        "id": str(profile.id),
        "revision": profile.revision,
        "agent_kind": profile.agent_kind,
        "model": settings.llm.model if profile.agent_kind == "openhands" else settings.acp_model,
    }
    return Selection(settings, identity, list(roots))


def native():
    selected = CURRENT.get()
    return selected is not None and selected.native
