"""
A2A Bridge — Expose and consume GemCode agents via Google's Agent2Agent protocol.

This module bridges the org system with ADK's native A2A support:
- `expose_agent()` — Turn an org member into a network-accessible A2A server
- `connect_remote_agent()` — Consume a remote A2A agent as an org member
- Auto-generate agent cards from org.json metadata

Requires: google-adk[a2a] (pip install google-adk[a2a])
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.org import OrgMember, find_member, resolve_fleet_root


def a2a_available() -> bool:
  """Check if A2A support is installed."""
  try:
    from google.adk.a2a.utils.agent_to_a2a import to_a2a  # noqa: F401
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent  # noqa: F401
    return True
  except ImportError:
    # Try auto-installing the a2a-sdk dependency
    try:
      import subprocess
      import sys
      subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "a2a-sdk>=0.3.4,<0.4.0", "--quiet"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
      )
      # Retry import after install
      from google.adk.a2a.utils.agent_to_a2a import to_a2a  # noqa: F401
      from google.adk.agents.remote_a2a_agent import RemoteA2aAgent  # noqa: F401
      return True
    except Exception:
      return False


def build_agent_card_dict(member: OrgMember, *, base_url: str = "") -> dict[str, Any]:
  """
  Build an A2A agent card dictionary from an org member.

  This is the metadata that describes what an agent can do,
  following the A2A protocol specification.
  """
  skills = []

  # Primary skill based on the member's role
  skills.append({
    "id": f"{member.name}_primary",
    "name": member.title,
    "description": member.description or f"Agent: {member.name} ({member.title})",
    "tags": [member.kind, "gemcode"],
  })

  url = base_url or f"http://localhost:0/a2a/{member.name}"

  return {
    "name": member.name,
    "description": member.description or f"{member.name} — {member.title}",
    "version": "1.0.0",
    "url": url,
    "capabilities": {
      "streaming": True,
      "pushNotifications": False,
    },
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": skills,
    "supportsAuthenticatedExtendedCard": False,
  }


def expose_member_as_a2a(
  cfg: GemCodeConfig,
  member_name: str,
  *,
  port: int = 0,
  host: str = "localhost",
) -> Any | None:
  """
  Expose an org member as an A2A server using ADK's to_a2a().

  Returns the Starlette app (can be served with uvicorn) or None if A2A unavailable.
  """
  if not a2a_available():
    return None

  from google.adk.a2a.utils.agent_to_a2a import to_a2a

  fleet_root = resolve_fleet_root(cfg.project_root)
  m = find_member(fleet_root, member_name)
  if m is None:
    return None

  # Build a real ADK agent for this member
  from gemcode.agent import build_root_agent
  from gemcode.tools import build_function_tools

  import copy
  agent_cfg = copy.deepcopy(cfg)
  tools = build_function_tools(agent_cfg, include_subtask=False)
  agent = build_root_agent(agent_cfg, _tools=tools)

  # Generate agent card
  base_url = f"http://{host}:{port}" if port else f"http://{host}"
  card_dict = build_agent_card_dict(m, base_url=base_url)

  # Create A2A app
  try:
    a2a_app = to_a2a(agent, port=port or 8001, agent_card=card_dict)
    return a2a_app
  except Exception:
    # Fallback: try without custom card
    try:
      a2a_app = to_a2a(agent, port=port or 8001)
      return a2a_app
    except Exception:
      return None


def create_remote_a2a_member(
  *,
  name: str,
  description: str,
  agent_card_url: str,
  use_legacy: bool = False,
) -> Any | None:
  """
  Create a RemoteA2aAgent that can be used as a sub-agent.

  This lets GemCode consume external A2A agents as if they were local org members.
  """
  if not a2a_available():
    return None

  try:
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

    remote_agent = RemoteA2aAgent(
      name=name,
      description=description,
      agent_card=agent_card_url,
      use_legacy=use_legacy,
    )
    return remote_agent
  except Exception:
    return None


def list_exposed_agents(cfg: GemCodeConfig) -> list[dict[str, Any]]:
  """List all org members that could be exposed via A2A."""
  from gemcode.org import list_members

  fleet_root = resolve_fleet_root(cfg.project_root)
  members = list_members(fleet_root)
  return [
    {
      "name": m.name,
      "title": m.title,
      "kind": m.kind,
      "description": m.description,
      "a2a_available": a2a_available(),
      "card": build_agent_card_dict(m),
    }
    for m in members
  ]


def make_a2a_tools(cfg: GemCodeConfig) -> list:
  """Build A2A-related tools for the agent."""

  def a2a_expose(member: str, port: int = 8001, host: str = "localhost") -> dict:
    """
    Expose an org member as an A2A server (network-accessible agent).

    Other GemCode instances or any A2A-compatible agent can then communicate
    with this member over HTTP.

    Args:
      member: Name or ID of the org member to expose.
      port: Port to serve on (default 8001).
      host: Host to bind to (default localhost).
    """
    if not a2a_available():
      return {"ok": False, "error": "A2A auto-install failed. Try: pip install a2a-sdk"}

    app = expose_member_as_a2a(cfg, member, port=port, host=host)
    if app is None:
      return {"ok": False, "error": f"Failed to expose member '{member}'. Check org_list() for available members."}

    return {
      "ok": True,
      "member": member,
      "url": f"http://{host}:{port}",
      "agent_card_url": f"http://{host}:{port}/.well-known/agent-card.json",
      "note": f"Start with: uvicorn ... --host {host} --port {port}",
    }

  def a2a_connect(name: str, description: str, agent_card_url: str) -> dict:
    """
    Connect to a remote A2A agent and register it as an org member.

    This lets you delegate tasks to agents running on other machines or
    in other frameworks (LangGraph, CrewAI, etc.) via the A2A protocol.

    Args:
      name: Local name for this remote agent.
      description: What this agent does.
      agent_card_url: URL to the agent's card (e.g., http://host:port/.well-known/agent-card.json)
    """
    if not a2a_available():
      return {"ok": False, "error": "A2A auto-install failed. Try: pip install a2a-sdk"}

    remote = create_remote_a2a_member(
      name=name,
      description=description,
      agent_card_url=agent_card_url,
    )
    if remote is None:
      return {"ok": False, "error": f"Failed to connect to remote agent at {agent_card_url}"}

    # Register in org
    from gemcode.org import hire_member
    fleet_root = resolve_fleet_root(cfg.project_root)
    m = hire_member(
      fleet_root,
      name=name,
      title=f"Remote A2A Agent",
      kind="subagent",
      description=f"[A2A] {description} (card: {agent_card_url})",
      reports_to="manager",
    )

    return {
      "ok": True,
      "member": m.to_dict() if hasattr(m, "to_dict") else {"name": name},
      "agent_card_url": agent_card_url,
      "note": "Use org_delegate to send tasks to this remote agent.",
    }

  def a2a_list() -> dict:
    """List all agents that can be exposed or are connected via A2A."""
    agents = list_exposed_agents(cfg)
    return {"ok": True, "agents": agents, "a2a_installed": a2a_available()}

  a2a_expose.__name__ = "a2a_expose"
  a2a_connect.__name__ = "a2a_connect"
  a2a_list.__name__ = "a2a_list"

  return [a2a_expose, a2a_connect, a2a_list]
