"""Track a background ``gemcode serve`` process for /serve slash and CLI helpers."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SERVE_STATE_NAME = "web-serve.json"
DEFAULT_SERVE_PORT = 3001


def serve_state_path(project_root: Path) -> Path:
  return project_root.expanduser().resolve() / ".gemcode" / SERVE_STATE_NAME


def read_serve_state(project_root: Path) -> dict[str, Any] | None:
  path = serve_state_path(project_root)
  if not path.is_file():
    return None
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None
  except (OSError, json.JSONDecodeError):
    return None


def write_serve_state(project_root: Path, payload: dict[str, Any]) -> None:
  path = serve_state_path(project_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def clear_serve_state(project_root: Path) -> None:
  path = serve_state_path(project_root)
  try:
    path.unlink(missing_ok=True)
  except OSError:
    pass


def pid_alive(pid: int) -> bool:
  if pid <= 0:
    return False
  try:
    os.kill(pid, 0)
    return True
  except OSError:
    return False


def probe_health(base_url: str, *, timeout_s: float = 2.0) -> dict[str, Any] | None:
  url = base_url.rstrip("/") + "/api/health"
  try:
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
      raw = resp.read().decode("utf-8", errors="replace")
      data = json.loads(raw)
      return data if isinstance(data, dict) else None
  except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
    return None


def serve_base_url(host: str, port: int) -> str:
  return f"http://{host}:{port}"


def is_serve_running(
  project_root: Path,
  *,
  host: str = "127.0.0.1",
  port: int = DEFAULT_SERVE_PORT,
) -> tuple[bool, dict[str, Any] | None]:
  state = read_serve_state(project_root)
  url = serve_base_url(host, port)
  if state:
    url = str(state.get("url") or url)
    pid = int(state.get("pid") or 0)
    if pid and pid_alive(pid):
      health = probe_health(url)
      if health and health.get("status") == "ok":
        return True, {**state, "health": health}
    clear_serve_state(project_root)

  health = probe_health(url)
  if health and health.get("status") == "ok":
    expected = str(project_root.expanduser().resolve())
    raw_root = health.get("project_root") or health.get("cwd")
    if raw_root:
      try:
        actual = str(Path(str(raw_root)).expanduser().resolve())
        if actual != expected:
          return False, {
            "url": url,
            "health": health,
            "external": True,
            "project_mismatch": True,
            "expected_root": expected,
            "actual_root": actual,
          }
      except (OSError, ValueError):
        pass
    return True, {"url": url, "health": health, "external": True}
  return False, state


def start_background_serve(
  project_root: Path,
  *,
  host: str = "127.0.0.1",
  port: int = DEFAULT_SERVE_PORT,
  session_id: str | None = None,
) -> dict[str, Any]:
  root = project_root.expanduser().resolve()
  running, info = is_serve_running(root, host=host, port=port)
  if info and info.get("project_mismatch"):
    return {"ok": False, "project_mismatch": True, **info}
  if running:
    return {"ok": True, "already_running": True, **(info or {})}

  preferred_port = port
  try:
    from gemcode.web.serve_bind import find_available_port

    port = find_available_port(host, preferred_port)
  except OSError as exc:
    return {"ok": False, "error": str(exc), "preferred_port": preferred_port}

  log_path = root / ".gemcode" / "web-serve.log"
  log_path.parent.mkdir(parents=True, exist_ok=True)
  log_fh = open(log_path, "a", encoding="utf-8")
  cmd = [
    sys.executable,
    "-m",
    "gemcode.cli",
    "serve",
    "-C",
    str(root),
    "--host",
    host,
    "--port",
    str(port),
  ]
  if session_id:
    cmd.extend(["--session-id", session_id])
  proc = subprocess.Popen(
    cmd,
    cwd=str(root),
    stdout=log_fh,
    stderr=subprocess.STDOUT,
    start_new_session=True,
  )
  url = serve_base_url(host, port)
  for _ in range(40):
    time.sleep(0.15)
    if not pid_alive(proc.pid):
      break
    health = probe_health(url)
    if health and health.get("status") == "ok":
      payload = {
        "pid": proc.pid,
        "host": host,
        "port": port,
        "url": url,
        "project_root": str(root),
        "session_id": session_id,
        "log": str(log_path),
      }
      write_serve_state(root, payload)
      result = {"ok": True, "started": True, **payload, "health": health}
      if port != preferred_port:
        result["preferred_port"] = preferred_port
        result["port_fallback"] = True
      return result
  if pid_alive(proc.pid):
    payload = {
      "pid": proc.pid,
      "host": host,
      "port": port,
      "url": url,
      "project_root": str(root),
      "session_id": session_id,
      "log": str(log_path),
      "warming": True,
    }
    write_serve_state(root, payload)
    result = {"ok": True, "started": True, **payload}
    if port != preferred_port:
      result["preferred_port"] = preferred_port
      result["port_fallback"] = True
    return result
  return {
    "ok": False,
    "error": f"Server failed to start — see {log_path}",
    "log": str(log_path),
  }


def stop_background_serve(project_root: Path) -> dict[str, Any]:
  root = project_root.expanduser().resolve()
  state = read_serve_state(root)
  if not state:
    return {"ok": True, "stopped": False, "message": "No recorded serve process for this project."}
  pid = int(state.get("pid") or 0)
  if pid and pid_alive(pid):
    try:
      os.kill(pid, signal.SIGTERM)
    except OSError as exc:
      return {"ok": False, "error": str(exc)}
    for _ in range(30):
      if not pid_alive(pid):
        break
      time.sleep(0.1)
    if pid_alive(pid):
      try:
        os.kill(pid, signal.SIGKILL)
      except OSError:
        pass
  clear_serve_state(root)
  return {"ok": True, "stopped": True, "pid": pid}
