"""
Auto-loads OpenAPI specs from `.gemcode/openapi/` as ADK OpenAPIToolset instances.

Inspired by the ADK community pattern: drop an OpenAPI spec in a directory and
the agent automatically gets REST API tools for it — no manual tool wiring needed.

Supported formats: .yaml, .yml, .json

Example:
  .gemcode/openapi/
  ├── github.yaml        → tools: mcp__github__list_repos, mcp__github__create_pr, …
  ├── sentry.json        → tools: mcp__sentry__list_issues, …
  └── internal_api.yaml  → tools from your company's internal REST API

The filename (without extension) is used as the tool name prefix.
So `github.yaml` → tools prefixed with `github_`.

Auth:
  Each spec file can have a matching `.auth` sidecar file with JSON:
  {
    "type": "api_key",        // "api_key" | "bearer" | "basic" | "oauth2"
    "header": "X-API-Key",
    "value": "${GITHUB_TOKEN}"  // ${ENV_VAR} is expanded from environment
  }
  For oauth2: {"type": "oauth2", "token_url": "...", "client_id": "...", "client_secret": "${VAR}"}

Requires: pip install google-adk (OpenAPIToolset is included in the main package)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
_OPENAPI_DIR = ".gemcode/openapi"


def _expand(value: str) -> str:
  return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def _load_auth(spec_path: Path):
  """Try to load auth config from <spec_stem>.auth next to the spec file."""
  auth_path = spec_path.with_suffix(".auth")
  if not auth_path.is_file():
    auth_path = spec_path.parent / (spec_path.stem + ".auth.json")
  if not auth_path.is_file():
    return None, None
  try:
    auth_data = json.loads(auth_path.read_text())
  except Exception:
    return None, None

  auth_type = auth_data.get("type", "").lower()
  try:
    from google.adk.auth import AuthCredential, AuthCredentialTypes
    if auth_type == "api_key":
      return None, AuthCredential(
          auth_type=AuthCredentialTypes.API_KEY,
          api_key=_expand(auth_data.get("value", "")),
      )
    if auth_type in ("bearer", "token"):
      return None, AuthCredential(
          auth_type=AuthCredentialTypes.HTTP,
          http={"scheme": "bearer", "token": _expand(auth_data.get("value", ""))},
      )
  except ImportError:
    pass
  return None, None


def load_openapi_toolsets(project_root: Path) -> list[Any]:
  """
  Scan .gemcode/openapi/ for spec files and return OpenAPIToolset instances.

  Returns an empty list if the directory doesn't exist or no valid specs are found.
  Errors loading individual specs are logged but don't crash GemCode.
  """
  openapi_dir = project_root / _OPENAPI_DIR
  if not openapi_dir.is_dir():
    return []

  try:
    from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import (
        OpenAPIToolset,
    )
  except ImportError:
    log.debug("[openapi] OpenAPIToolset not available in this ADK version — skipping")
    return []

  toolsets: list[Any] = []

  for spec_file in sorted(openapi_dir.iterdir()):
    if spec_file.suffix.lower() not in (".yaml", ".yml", ".json"):
      continue
    if spec_file.stem.endswith(".auth"):
      continue

    prefix = spec_file.stem  # e.g. "github" from "github.yaml"
    fmt = "json" if spec_file.suffix.lower() == ".json" else "yaml"

    try:
      spec_str = spec_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
      log.warning("[openapi] Could not read %s: %s", spec_file.name, e)
      continue

    _, auth_credential = _load_auth(spec_file)

    try:
      kwargs: dict[str, Any] = dict(spec_str=spec_str, spec_str_type=fmt)
      if auth_credential is not None:
        kwargs["auth_credential"] = auth_credential
      toolset = OpenAPIToolset(**kwargs)
      toolsets.append(toolset)
      log.info("[openapi] Loaded spec '%s' from %s", prefix, spec_file.name)
    except Exception as exc:
      log.warning("[openapi] Failed to load %s: %s", spec_file.name, exc)

  return toolsets
