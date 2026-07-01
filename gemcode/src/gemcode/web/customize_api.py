"""Skills and MCP management for the GemCode web Customize UI."""

from __future__ import annotations

import json
import os
import re
import shutil
import base64
import io
import zipfile
from pathlib import Path
from typing import Any

from gemcode.org import resolve_fleet_root
from gemcode.skills import (
  _BUILTIN_SKILLS,
  _is_valid_name,
  _parse_frontmatter,
  discover_skill_metas,
  list_supporting_files,
  load_skill,
)

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


from gemcode.web.project_root import resolve_web_project_root


def _resolve_root(raw_path: str | None) -> Path:
  return resolve_web_project_root(raw_path)


def _skill_source(skill_dir: Path, project_root: Path, name: str) -> str:
  if name in _BUILTIN_SKILLS and skill_dir.resolve() == project_root.resolve():
    return "built-in"
  personal = (Path.home() / ".gemcode" / "skills").resolve()
  try:
    skill_dir.resolve().relative_to(personal)
    return "personal"
  except ValueError:
    pass
  return "project"


def _skills_base_dir(project_root: Path, scope: str) -> Path:
  if scope == "personal":
    return Path.home() / ".gemcode" / "skills"
  fleet_root = resolve_fleet_root(project_root)
  return fleet_root / ".gemcode" / "skills"


def _skill_template(name: str, description: str) -> str:
  return (
    "---\n"
    f"name: {name}\n"
    f"description: {description}\n"
    "disable-model-invocation: false\n"
    "---\n\n"
    f"# GemSkill: {name}\n\n"
    "## Purpose\n"
    f"{description}\n\n"
    "## When to use\n"
    "- Use this skill when the user request matches the Purpose above.\n\n"
    "## Workflow\n"
    "1. Clarify the goal from the user's request.\n"
    "2. Gather evidence with read/search tools before proposing changes.\n"
    "3. Execute the smallest correct set of steps.\n"
    "4. Verify when applicable and summarize results.\n"
  )


def skills_snapshot(project_root: Path) -> dict[str, Any]:
  fleet_root = resolve_fleet_root(project_root)
  metas = discover_skill_metas(project_root)
  rows: list[dict[str, Any]] = []
  for name, (meta, skill_dir) in sorted(metas.items(), key=lambda x: x[0]):
    source = _skill_source(skill_dir, project_root, name)
    skill_path = ""
    if source != "built-in" and skill_dir.is_dir():
      skill_path = str((skill_dir / "SKILL.md").resolve())
    rows.append(
      {
        "name": meta.name,
        "description": meta.description,
        "source": source,
        "user_invocable": meta.user_invocable,
        "disable_model_invocation": meta.disable_model_invocation,
        "skill_path": skill_path,
        "skill_dir": str(skill_dir.resolve()) if source != "built-in" else "",
      }
    )
  return {
    "ok": True,
    "fleet_root": str(fleet_root),
    "skills_dir_hint": str(fleet_root / ".gemcode" / "skills"),
    "personal_skills_dir": str(Path.home() / ".gemcode" / "skills"),
    "total": len(rows),
    "builtin_count": sum(1 for r in rows if r["source"] == "built-in"),
    "personal_count": sum(1 for r in rows if r["source"] == "personal"),
    "project_count": sum(1 for r in rows if r["source"] == "project"),
    "skills": rows,
  }


def skill_detail_snapshot(project_root: Path, name: str) -> dict[str, Any]:
  skill = load_skill(project_root, name)
  if skill is None:
    return {"ok": False, "error": f"Skill not found: {name}"}
  source = _skill_source(skill.skill_dir, project_root, skill.meta.name)
  supporting = list_supporting_files(skill) if source != "built-in" else []
  return {
    "ok": True,
    "name": skill.meta.name,
    "description": skill.meta.description,
    "body": skill.body_markdown,
    "source": source,
    "user_invocable": skill.meta.user_invocable,
    "disable_model_invocation": skill.meta.disable_model_invocation,
    "skill_path": str(skill.skill_md),
    "skill_dir": str(skill.skill_dir),
    "supporting_files": supporting,
  }


def skill_create_action(
  project_root: Path,
  *,
  name: str,
  description: str,
  scope: str = "project",
  body: str = "",
) -> dict[str, Any]:
  skill_name = (name or "").strip().lower()
  if not _is_valid_name(skill_name):
    return {
      "ok": False,
      "error": "Invalid skill name. Use lowercase letters, numbers, and hyphens only (max 64 characters).",
    }
  if scope not in ("project", "personal"):
    return {"ok": False, "error": "scope must be project or personal"}
  desc = (description or "").strip() or f"Describe what the {skill_name} skill does."
  skill_dir = _skills_base_dir(project_root, scope) / skill_name
  skill_md = skill_dir / "SKILL.md"
  if skill_md.is_file():
    return {"ok": False, "error": f"Skill already exists: {skill_name}"}
  try:
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = (body or "").strip() or _skill_template(skill_name, desc)
    if not content.lstrip().startswith("---"):
      content = _skill_template(skill_name, desc)
    skill_md.write_text(content, encoding="utf-8")
  except OSError as exc:
    return {"ok": False, "error": str(exc)}
  return {
    "ok": True,
    "name": skill_name,
    "skill_path": str(skill_md.resolve()),
    "message": f"Created skill {skill_name}",
  }


def skill_update_action(
  project_root: Path,
  *,
  name: str,
  description: str | None = None,
  body: str | None = None,
) -> dict[str, Any]:
  skill_name = (name or "").strip().lower()
  skill = load_skill(project_root, skill_name)
  if skill is None:
    return {"ok": False, "error": f"Skill not found: {skill_name}"}
  source = _skill_source(skill.skill_dir, project_root, skill_name)
  if source == "built-in":
    return {"ok": False, "error": "Built-in skills cannot be edited from the web UI."}
  skill_md = skill.skill_dir / "SKILL.md"
  if not skill_md.is_file():
    return {"ok": False, "error": "SKILL.md not found on disk"}
  try:
    text = skill_md.read_text(encoding="utf-8")
    fm, existing_body = _parse_frontmatter(text)
    new_desc = description.strip() if isinstance(description, str) and description.strip() else fm.get("description", skill.meta.description)
    new_body = body if isinstance(body, str) else existing_body
    fm["name"] = skill_name
    fm["description"] = new_desc
    front_lines = ["---"]
    for k, v in fm.items():
      front_lines.append(f"{k}: {v}")
    front_lines.append("---")
    skill_md.write_text("\n".join(front_lines) + "\n\n" + new_body.strip() + "\n", encoding="utf-8")
  except OSError as exc:
    return {"ok": False, "error": str(exc)}
  return {"ok": True, "name": skill_name, "message": f"Updated skill {skill_name}"}


def _resolve_skill_supporting_path(skill_dir: Path, rel_path: str) -> Path | None:
  """Resolve a relative path inside a skill folder; block traversal and SKILL.md."""
  clean = (rel_path or "").replace("\\", "/").strip().lstrip("/")
  if not clean or clean == "SKILL.md":
    return None
  parts = [p for p in clean.split("/") if p and p != "."]
  if any(p == ".." for p in parts):
    return None
  target = (skill_dir / Path(*parts)).resolve()
  try:
    target.relative_to(skill_dir.resolve())
  except ValueError:
    return None
  if not target.is_file():
    return None
  return target


def skill_read_file_action(project_root: Path, *, name: str, file_path: str) -> dict[str, Any]:
  skill_name = (name or "").strip().lower()
  skill = load_skill(project_root, skill_name)
  if skill is None:
    return {"ok": False, "error": f"Skill not found: {skill_name}"}
  source = _skill_source(skill.skill_dir, project_root, skill_name)
  if source == "built-in":
    return {"ok": False, "error": "Built-in skill files cannot be opened from the web UI."}
  target = _resolve_skill_supporting_path(skill.skill_dir, file_path)
  if target is None:
    return {"ok": False, "error": f"File not found: {file_path}"}
  try:
    content = target.read_text(encoding="utf-8")
  except UnicodeDecodeError:
    return {"ok": False, "error": "This file is not UTF-8 text and cannot be edited in the browser."}
  except OSError as exc:
    return {"ok": False, "error": str(exc)}
  return {
    "ok": True,
    "name": skill_name,
    "file_path": file_path.replace("\\", "/"),
    "absolute_path": str(target),
    "content": content,
  }


def skill_update_file_action(
  project_root: Path,
  *,
  name: str,
  file_path: str,
  content: str,
) -> dict[str, Any]:
  skill_name = (name or "").strip().lower()
  skill = load_skill(project_root, skill_name)
  if skill is None:
    return {"ok": False, "error": f"Skill not found: {skill_name}"}
  source = _skill_source(skill.skill_dir, project_root, skill_name)
  if source == "built-in":
    return {"ok": False, "error": "Built-in skill files cannot be edited."}
  target = _resolve_skill_supporting_path(skill.skill_dir, file_path)
  if target is None:
    clean = (file_path or "").replace("\\", "/").strip().lstrip("/")
    parts = [p for p in clean.split("/") if p and p != "."]
    if not parts or clean == "SKILL.md" or any(p == ".." for p in parts):
      return {"ok": False, "error": f"Invalid file path: {file_path}"}
    target = (skill.skill_dir / Path(*parts)).resolve()
    try:
      target.relative_to(skill.skill_dir.resolve())
    except ValueError:
      return {"ok": False, "error": f"Invalid file path: {file_path}"}
  try:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if isinstance(content, str) else "", encoding="utf-8")
  except OSError as exc:
    return {"ok": False, "error": str(exc)}
  return {
    "ok": True,
    "name": skill_name,
    "file_path": str(target.relative_to(skill.skill_dir)).replace("\\", "/"),
    "message": "File saved",
  }


def skill_delete_action(project_root: Path, *, name: str) -> dict[str, Any]:
  skill_name = (name or "").strip().lower()
  metas = discover_skill_metas(project_root)
  if skill_name not in metas:
    return {"ok": False, "error": f"Skill not found: {skill_name}"}
  meta, skill_dir = metas[skill_name]
  source = _skill_source(skill_dir, project_root, skill_name)
  if source == "built-in":
    return {"ok": False, "error": "Built-in skills cannot be deleted."}
  if not skill_dir.is_dir() or skill_dir == project_root:
    return {"ok": False, "error": "Cannot delete this skill."}
  try:
    shutil.rmtree(skill_dir)
  except OSError as exc:
    return {"ok": False, "error": str(exc)}
  return {"ok": True, "name": skill_name, "message": f"Deleted skill {skill_name}"}


def _safe_zip_member_path(dest_dir: Path, member_name: str) -> Path | None:
  """Resolve a zip member to a path under dest_dir, or None if unsafe."""
  clean = member_name.replace("\\", "/").lstrip("/")
  if not clean or clean.endswith("/"):
    return None
  parts = [p for p in clean.split("/") if p and p != "."]
  if any(p == ".." for p in parts):
    return None
  target = (dest_dir / Path(*parts)).resolve()
  try:
    target.relative_to(dest_dir.resolve())
  except ValueError:
    return None
  return target


def _find_skill_md_prefix(names: list[str]) -> str | None:
  """Return zip path prefix (with trailing slash) for the skill folder, or '' for root SKILL.md."""
  skill_paths = [n.replace("\\", "/") for n in names if n.replace("\\", "/").rstrip("/").endswith("SKILL.md")]
  if not skill_paths:
    return None
  if len(skill_paths) == 1:
    p = skill_paths[0]
    if p == "SKILL.md":
      return ""
    if p.endswith("/SKILL.md"):
      return p[: -len("SKILL.md")]
  # Multiple SKILL.md — pick shallowest common folder
  p = min(skill_paths, key=lambda x: x.count("/"))
  if p == "SKILL.md":
    return ""
  if "/SKILL.md" in p:
    return p.split("/SKILL.md")[0] + "/"
  return None


def skill_import_action(
  project_root: Path,
  *,
  file_b64: str,
  filename: str,
  scope: str = "project",
  overwrite: bool = False,
) -> dict[str, Any]:
  if scope not in ("project", "personal"):
    return {"ok": False, "error": "scope must be project or personal"}
  raw_name = (filename or "").strip().lower()
  if not raw_name.endswith((".skill", ".zip")):
    return {"ok": False, "error": "Upload a .skill or .zip file (zip archive with SKILL.md)"}
  try:
    payload = base64.b64decode(file_b64, validate=True)
  except Exception:
    return {"ok": False, "error": "Invalid file encoding"}
  if len(payload) > 20 * 1024 * 1024:
    return {"ok": False, "error": "Skill file is too large (max 20 MB)"}
  try:
    zf = zipfile.ZipFile(io.BytesIO(payload))
  except zipfile.BadZipFile:
    return {"ok": False, "error": "Not a valid zip archive (.skill files are zip bundles)"}

  names = [n for n in zf.namelist() if not n.endswith("/")]
  prefix = _find_skill_md_prefix(names)
  if prefix is None:
    return {"ok": False, "error": "Archive must contain SKILL.md at the root or in one folder"}

  skill_md_entry = f"{prefix}SKILL.md" if prefix else "SKILL.md"
  try:
    skill_md_text = zf.read(skill_md_entry).decode("utf-8")
  except (KeyError, UnicodeDecodeError) as exc:
    return {"ok": False, "error": f"Could not read SKILL.md: {exc}"}

  fm, _ = _parse_frontmatter(skill_md_text)
  stem = Path(filename).stem.lower().replace("_", "-")
  skill_name = str(fm.get("name") or stem or "imported-skill").strip().lower()
  if not _is_valid_name(skill_name):
    return {
      "ok": False,
      "error": "Skill name from SKILL.md is invalid. Use lowercase letters, numbers, and hyphens.",
    }

  skill_dir = _skills_base_dir(project_root, scope) / skill_name
  if skill_dir.is_dir() and not overwrite:
    return {
      "ok": False,
      "error": f"Skill '{skill_name}' already exists. Enable overwrite or delete it first.",
    }

  try:
    if skill_dir.is_dir():
      shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for member in names:
      norm = member.replace("\\", "/")
      if prefix and not norm.startswith(prefix):
        continue
      rel = norm[len(prefix) :] if prefix else norm
      if not rel or rel.endswith("/"):
        continue
      target = _safe_zip_member_path(skill_dir, rel)
      if target is None:
        continue
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_bytes(zf.read(member))
      extracted += 1
    if not (skill_dir / "SKILL.md").is_file():
      return {"ok": False, "error": "SKILL.md was not extracted correctly"}
  except OSError as exc:
    return {"ok": False, "error": str(exc)}

  return {
    "ok": True,
    "name": skill_name,
    "skill_path": str((skill_dir / "SKILL.md").resolve()),
    "skill_dir": str(skill_dir.resolve()),
    "files_extracted": extracted,
    "message": f"Imported skill {skill_name}",
  }


def _server_summary(server: dict[str, Any]) -> tuple[str, str]:
  name = str(server.get("name") or "server")
  if isinstance(server.get("stdio"), dict):
    stdio = server["stdio"]
    cmd = str(stdio.get("command") or "")
    args = stdio.get("args") or []
    arg_str = " ".join(str(a) for a in args) if isinstance(args, list) else ""
    return name, f"{cmd} {arg_str}".strip()
  if isinstance(server.get("http"), dict):
    return name, str(server["http"].get("url") or "")
  if isinstance(server.get("sse"), dict):
    return name, str(server["sse"].get("url") or "")
  return name, "Unknown connection type"


def mcp_snapshot(project_root: Path) -> dict[str, Any]:
  fleet_root = resolve_fleet_root(project_root)
  mcp_path = fleet_root / ".gemcode" / "mcp.json"
  rows: list[dict[str, Any]] = []
  raw_data: dict[str, Any] = {"servers": []}
  parse_error: str | None = None
  if mcp_path.is_file():
    try:
      raw_data = json.loads(mcp_path.read_text(encoding="utf-8"))
      if not isinstance(raw_data, dict):
        raw_data = {"servers": []}
      for s in raw_data.get("servers") or []:
        if not isinstance(s, dict):
          continue
        name, summary = _server_summary(s)
        conn = "stdio" if "stdio" in s else "http" if "http" in s else "sse" if "sse" in s else "unknown"
        tool_filter = s.get("tools")
        rows.append(
          {
            "name": name,
            "connection_type": conn,
            "summary": summary,
            "tools_filter": tool_filter if isinstance(tool_filter, list) else None,
            "raw": s,
          }
        )
    except json.JSONDecodeError as exc:
      parse_error = str(exc)
  return {
    "ok": True,
    "fleet_root": str(fleet_root),
    "mcp_file": str(mcp_path),
    "mcp_exists": mcp_path.is_file(),
    "parse_error": parse_error,
    "server_count": len(rows),
    "servers": rows,
    "raw": raw_data,
    "install_hint": "pip install gemcode[mcp]",
  }


def _validate_mcp_server(server: dict[str, Any]) -> str | None:
  if not isinstance(server, dict):
    return "Each MCP server must be an object"
  name = str(server.get("name") or "").strip()
  if not name:
    return "Server name is required"
  kinds = sum(1 for k in ("stdio", "http", "sse") if k in server)
  if kinds != 1:
    return f"MCP server '{name}' must have exactly one of stdio, http, or sse"
  if "stdio" in server:
    stdio = server["stdio"]
    if not isinstance(stdio, dict) or not str(stdio.get("command") or "").strip():
      return f"MCP server '{name}' needs a command"
  if "http" in server:
    http = server["http"]
    if not isinstance(http, dict) or not str(http.get("url") or "").strip():
      return f"MCP server '{name}' needs a URL"
  if "sse" in server:
    sse = server["sse"]
    if not isinstance(sse, dict) or not str(sse.get("url") or "").strip():
      return f"MCP server '{name}' needs a URL"
  return None


def mcp_save_action(project_root: Path, servers: list[dict[str, Any]]) -> dict[str, Any]:
  fleet_root = resolve_fleet_root(project_root)
  mcp_path = fleet_root / ".gemcode" / "mcp.json"
  if not isinstance(servers, list):
    return {"ok": False, "error": "servers must be a list"}
  for s in servers:
    err = _validate_mcp_server(s)
    if err:
      return {"ok": False, "error": err}
  payload = {"servers": servers}
  try:
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
  except OSError as exc:
    return {"ok": False, "error": str(exc)}
  return {
    "ok": True,
    "mcp_file": str(mcp_path),
    "server_count": len(servers),
    "message": "Saved MCP config",
  }


def mcp_add_stdio_action(
  project_root: Path,
  *,
  name: str,
  command: str,
  args: list[str] | None = None,
) -> dict[str, Any]:
  snap = mcp_snapshot(project_root)
  servers = [s["raw"] for s in snap.get("servers", []) if isinstance(s.get("raw"), dict)]
  conn_name = (name or "").strip()
  if not conn_name:
    return {"ok": False, "error": "Server name is required"}
  cmd = (command or "").strip()
  if not cmd:
    return {"ok": False, "error": "Command is required"}
  if any(str(s.get("name") or "") == conn_name for s in servers):
    return {"ok": False, "error": f"MCP server '{conn_name}' already exists"}
  servers.append(
    {
      "name": conn_name,
      "stdio": {"command": cmd, "args": args or []},
    }
  )
  return mcp_save_action(project_root, servers)


def mcp_remove_action(project_root: Path, *, name: str) -> dict[str, Any]:
  snap = mcp_snapshot(project_root)
  servers = [s["raw"] for s in snap.get("servers", []) if isinstance(s.get("raw"), dict)]
  conn_name = (name or "").strip()
  kept = [s for s in servers if str(s.get("name") or "") != conn_name]
  if len(kept) == len(servers):
    return {"ok": False, "error": f"MCP server not found: {conn_name}"}
  return mcp_save_action(project_root, kept)


def handle_customize_get(kind: str, raw_path: str | None, *, skill_name: str | None = None) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}
  if kind == "skills":
    if skill_name:
      payload = skill_detail_snapshot(root, skill_name)
      return (200 if payload.get("ok") else 404), payload
    return 200, skills_snapshot(root)
  if kind == "mcp":
    return 200, mcp_snapshot(root)
  return 404, {"ok": False, "error": "unknown resource"}


def handle_skills_post(data: dict[str, Any], raw_path: str) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}

  action = str(data.get("action") or "list").strip().lower()
  if action == "list":
    return 200, skills_snapshot(root)
  if action == "get":
    name = str(data.get("name") or "").strip().lower()
    payload = skill_detail_snapshot(root, name)
    return (200 if payload.get("ok") else 404), payload
  if action == "create":
    payload = skill_create_action(
      root,
      name=str(data.get("name") or ""),
      description=str(data.get("description") or ""),
      scope=str(data.get("scope") or "project"),
      body=str(data.get("body") or ""),
    )
    return (200 if payload.get("ok") else 400), payload
  if action == "update":
    payload = skill_update_action(
      root,
      name=str(data.get("name") or ""),
      description=data.get("description") if "description" in data else None,
      body=data.get("body") if "body" in data else None,
    )
    return (200 if payload.get("ok") else 400), payload
  if action == "delete":
    payload = skill_delete_action(root, name=str(data.get("name") or ""))
    return (200 if payload.get("ok") else 400), payload
  if action == "import":
    payload = skill_import_action(
      root,
      file_b64=str(data.get("file_b64") or ""),
      filename=str(data.get("filename") or "skill.skill"),
      scope=str(data.get("scope") or "project"),
      overwrite=bool(data.get("overwrite")),
    )
    return (200 if payload.get("ok") else 400), payload
  if action == "read_file":
    payload = skill_read_file_action(
      root,
      name=str(data.get("name") or ""),
      file_path=str(data.get("file_path") or ""),
    )
    return (200 if payload.get("ok") else 404), payload
  if action == "update_file":
    payload = skill_update_file_action(
      root,
      name=str(data.get("name") or ""),
      file_path=str(data.get("file_path") or ""),
      content=str(data.get("content") if data.get("content") is not None else ""),
    )
    return (200 if payload.get("ok") else 400), payload
  return 400, {"ok": False, "error": "action must be list, get, create, update, delete, import, read_file, or update_file"}


def handle_mcp_post(data: dict[str, Any], raw_path: str) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}

  action = str(data.get("action") or "list").strip().lower()
  if action == "list":
    return 200, mcp_snapshot(root)
  if action == "save":
    servers = data.get("servers")
    if not isinstance(servers, list):
      return 400, {"ok": False, "error": "servers must be a list"}
    payload = mcp_save_action(root, servers)
    return (200 if payload.get("ok") else 400), payload
  if action == "add_stdio":
    args_raw = data.get("args")
    args = args_raw if isinstance(args_raw, list) else []
    if isinstance(data.get("args_text"), str):
      args = [a for a in str(data["args_text"]).split() if a]
    payload = mcp_add_stdio_action(
      root,
      name=str(data.get("name") or ""),
      command=str(data.get("command") or ""),
      args=[str(a) for a in args],
    )
    return (200 if payload.get("ok") else 400), payload
  if action == "remove":
    payload = mcp_remove_action(root, name=str(data.get("name") or ""))
    return (200 if payload.get("ok") else 400), payload
  if action == "reload":
    return 200, {
      "ok": True,
      "message": "MCP servers will reload on the next chat turn (same as /mcp reload).",
      "force_rebuild_runner": True,
    }
  return 400, {"ok": False, "error": "action must be list, save, add_stdio, remove, or reload"}


def handle_credentials_post(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  from gemcode.credentials import save_google_api_key_to_user_store

  key = str(data.get("google_api_key") or data.get("api_key") or "").strip()
  if not key:
    return 400, {"ok": False, "error": "API key is required"}
  try:
    save_google_api_key_to_user_store(key)
    os.environ["GOOGLE_API_KEY"] = key
    if not os.environ.get("GEMINI_API_KEY"):
      os.environ["GEMINI_API_KEY"] = key
  except OSError as exc:
    return 500, {"ok": False, "error": str(exc)}
  return 200, {"ok": True, "message": "Gemini API key saved", "has_api_key": True}
