"""HTTP handlers for GemCode runtime (Kaira IPC): jobs, bus, status, launch."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from gemcode.fleet_reports import preview_fleet_inbox
from gemcode.org import resolve_fleet_root
from gemcode.web.project_root import resolve_web_project_root

_kaira_procs: dict[str, subprocess.Popen[Any]] = {}
_kaira_options: dict[str, dict[str, Any]] = {}
_kaira_lock = threading.Lock()


def _resolve_root(raw_path: str) -> Path:
  return resolve_web_project_root(raw_path)


def _proc_key(project_root: Path) -> str:
  return str(project_root.resolve())


def _gemcode_src() -> Path:
  return Path(__file__).resolve().parents[1]


def _subprocess_env(project_root: Path) -> dict[str, str]:
  env = os.environ.copy()
  src = _gemcode_src()
  prev = env.get("PYTHONPATH", "")
  env["PYTHONPATH"] = str(src) if not prev else f"{src}{os.pathsep}{prev}"
  env["GEMCODE_WEB_PROJECT_ROOT"] = str(project_root)
  if not env.get("GOOGLE_API_KEY") and env.get("GEMINI_API_KEY"):
    env["GOOGLE_API_KEY"] = env["GEMINI_API_KEY"]
  try:
    from gemcode.credentials import apply_saved_google_api_key_to_environ

    apply_saved_google_api_key_to_environ()
    if os.environ.get("GOOGLE_API_KEY"):
      env["GOOGLE_API_KEY"] = os.environ["GOOGLE_API_KEY"]
  except Exception:
    pass
  return env


def _parse_start_options(data: dict[str, Any]) -> dict[str, Any]:
  super_mode = bool(data.get("super"))
  yes = bool(data.get("yes"))
  interactive = bool(data.get("interactive_ask"))
  if super_mode:
    yes = False
    interactive = False
  elif yes:
    interactive = False
  cap = str(data.get("capability_mode") or "").strip() or None
  if cap == "auto":
    cap = None
  return {
    "super": super_mode,
    "yes": yes,
    "interactive_ask": interactive,
    "automations": bool(data.get("automations")),
    "deep_research": bool(data.get("deep_research")),
    "maps_grounding": bool(data.get("maps_grounding")),
    "embeddings": bool(data.get("embeddings")),
    "capability_mode": cap,
    "model_mode": str(data.get("model_mode") or "").strip() or None,
    "concurrency": max(1, min(8, int(data.get("concurrency") or 2))),
  }


def build_kaira_argv(project_root: Path, options: dict[str, Any]) -> list[str]:
  argv = [sys.executable, "-m", "gemcode.cli", "kaira", "-C", str(project_root)]
  if options.get("super"):
    argv.append("--super")
  elif options.get("yes"):
    argv.append("--yes")
  if options.get("interactive_ask"):
    argv.append("--interactive-ask")
  if options.get("automations"):
    argv.append("--automations")
  if options.get("deep_research"):
    argv.append("--deep-research")
  if options.get("maps_grounding"):
    argv.append("--maps-grounding")
  if options.get("embeddings"):
    argv.append("--embeddings")
  cap = options.get("capability_mode")
  if cap:
    argv.extend(["--capability-mode", str(cap)])
  mm = options.get("model_mode")
  if mm:
    argv.extend(["--model-mode", str(mm)])
  argv.extend(["--concurrency", str(int(options.get("concurrency") or 2))])
  return argv


def build_launch_hint(project_root: Path, options: dict[str, Any]) -> str:
  parts = ["gemcode", "kaira", "-C", shlex.quote(str(project_root))]
  if options.get("super"):
    parts.append("--super")
  elif options.get("yes"):
    parts.append("--yes")
  if options.get("interactive_ask"):
    parts.append("--interactive-ask")
  if options.get("automations"):
    parts.append("--automations")
  if options.get("deep_research"):
    parts.append("--deep-research")
  if options.get("maps_grounding"):
    parts.append("--maps-grounding")
  if options.get("embeddings"):
    parts.append("--embeddings")
  cap = options.get("capability_mode")
  if cap:
    parts.extend(["--capability-mode", shlex.quote(str(cap))])
  mm = options.get("model_mode")
  if mm:
    parts.extend(["--model-mode", shlex.quote(str(mm))])
  parts.extend(["--concurrency", str(int(options.get("concurrency") or 2))])
  return " ".join(parts)


def _wait_for_socket(sock: Path, *, timeout_s: float = 25.0) -> bool:
  deadline = time.time() + timeout_s
  while time.time() < deadline:
    if sock.is_file():
      return True
    time.sleep(0.2)
  return False


def _cleanup_proc(key: str) -> None:
  proc = _kaira_procs.pop(key, None)
  _kaira_options.pop(key, None)
  if proc is None:
    return
  if proc.poll() is None:
    try:
      proc.terminate()
      proc.wait(timeout=3)
    except Exception:
      try:
        proc.kill()
      except Exception:
        pass


def _managed_proc_status(key: str) -> tuple[bool, int | None, dict[str, Any]]:
  proc = _kaira_procs.get(key)
  opts = _kaira_options.get(key, {})
  if proc is None:
    return False, None, opts
  code = proc.poll()
  if code is not None:
    _kaira_procs.pop(key, None)
    return False, None, opts
  return True, proc.pid, opts


def start_runtime(project_root: Path, options: dict[str, Any]) -> dict[str, Any]:
  from gemcode.kaira_ipc import fleet_manager_ipc_path_for_workspace

  if not (
    os.environ.get("GOOGLE_API_KEY")
    or os.environ.get("GEMINI_API_KEY")
    or _subprocess_env(project_root).get("GOOGLE_API_KEY")
  ):
    return {
      "ok": False,
      "error": "GOOGLE_API_KEY is not set — add it in Settings or your .env file",
    }

  sock = fleet_manager_ipc_path_for_workspace(project_root)
  key = _proc_key(project_root)

  with _kaira_lock:
    managed, pid, _ = _managed_proc_status(key)
    if sock.is_file():
      return {
        "ok": True,
        "already_running": True,
        "runtime_running": True,
        "socket": str(sock),
        "managed_by_web": managed,
        "pid": pid,
        "launch_options": _kaira_options.get(key, options),
        "launch_hint": build_launch_hint(project_root, _kaira_options.get(key, options)),
      }

    _cleanup_proc(key)

    fleet_root = resolve_fleet_root(project_root)
    log_dir = fleet_root / ".gemcode"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "kaira-web.log"
    argv = build_kaira_argv(project_root, options)
    try:
      log_f = open(log_path, "a", encoding="utf-8")
      proc = subprocess.Popen(
        argv,
        cwd=str(project_root),
        env=_subprocess_env(project_root),
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
      )
    except OSError as exc:
      return {"ok": False, "error": f"Failed to start runtime: {exc}"}

    _kaira_procs[key] = proc
    _kaira_options[key] = dict(options)

  if not _wait_for_socket(sock):
    with _kaira_lock:
      managed, pid, stored = _managed_proc_status(key)
      err_tail = ""
      try:
        if log_path.is_file():
          err_tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
      except Exception:
        pass
      if not managed:
        return {
          "ok": False,
          "error": "Runtime process exited before the IPC socket was ready",
          "log_file": str(log_path),
          "log_tail": err_tail,
        }
    return {
      "ok": False,
      "error": "Timed out waiting for runtime socket — check kaira-web.log",
      "socket": str(sock),
      "log_file": str(log_path),
      "pid": pid,
    }

  return {
    "ok": True,
    "runtime_running": True,
    "managed_by_web": True,
    "pid": _kaira_procs.get(key).pid if _kaira_procs.get(key) else None,
    "socket": str(sock),
    "launch_options": options,
    "launch_hint": build_launch_hint(project_root, options),
    "log_file": str(log_path),
  }


def stop_runtime(project_root: Path) -> dict[str, Any]:
  key = _proc_key(project_root)
  with _kaira_lock:
    proc = _kaira_procs.get(key)
    if proc is None or proc.poll() is not None:
      _cleanup_proc(key)
      return {
        "ok": True,
        "stopped": False,
        "note": "No runtime process was started from the web UI for this workspace",
      }
    try:
      proc.terminate()
      proc.wait(timeout=5)
    except Exception:
      try:
        proc.kill()
      except Exception:
        pass
    _cleanup_proc(key)
  return {"ok": True, "stopped": True}


async def _kaira_request(project_root: Path, *, action: str, **payload: Any) -> dict[str, Any]:
  from gemcode.kaira_client import KairaIpcClient
  from gemcode.kaira_ipc import fleet_manager_ipc_path_for_workspace

  sock = fleet_manager_ipc_path_for_workspace(project_root)
  if not sock.is_file():
    return {
      "ok": False,
      "error": "runtime not running — start with: gemcode kaira -C <project>",
      "socket": str(sock),
      "socket_exists": False,
    }
  client = await KairaIpcClient.connect(socket_path=sock)
  try:
    return await client.request(action=action, **payload)
  finally:
    await client.close()


def runtime_status(project_root: Path) -> dict[str, Any]:
  from gemcode.kaira_ipc import fleet_manager_ipc_path_for_workspace

  fleet_root = resolve_fleet_root(project_root)
  sock = fleet_manager_ipc_path_for_workspace(project_root)
  key = _proc_key(project_root)
  managed, pid, opts = _managed_proc_status(key)
  default_opts = _parse_start_options({"yes": True})
  active_opts = opts or default_opts
  return {
    "ok": True,
    "fleet_root": str(fleet_root),
    "socket": str(sock),
    "socket_exists": sock.is_file(),
    "runtime_running": sock.is_file(),
    "managed_by_web": managed,
    "pid": pid,
    "launch_options": active_opts if sock.is_file() else default_opts,
    "launch_hint": build_launch_hint(project_root, active_opts),
    "log_file": str(fleet_root / ".gemcode" / "kaira-web.log"),
  }


def handle_runtime_get(kind: str, raw_path: str | None) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path or "")
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}

  if kind == "status":
    return 200, runtime_status(root)
  if kind == "inbox":
    preview = preview_fleet_inbox(root, max_chars=12000)
    return 200, {"ok": True, "preview": preview, "fleet_root": str(resolve_fleet_root(root))}
  return 404, {"ok": False, "error": f"unknown runtime GET kind: {kind}"}


def handle_runtime_post(data: dict[str, Any], raw_path: str) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}

  action = str(data.get("action") or "").strip().lower()

  if action == "status":
    return 200, runtime_status(root)

  if action == "start":
    options = _parse_start_options(data)
    payload = start_runtime(root, options)
    return (200 if payload.get("ok") else 503), payload

  if action == "stop":
    return 200, stop_runtime(root)

  if action == "inbox":
    preview = preview_fleet_inbox(root, max_chars=12000)
    return 200, {"ok": True, "preview": preview}

  if action == "list_jobs":
    limit = int(data.get("limit") or 30)
    try:
      res = asyncio.run(_kaira_request(root, action="list_jobs", limit=limit))
    except Exception as exc:
      return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return (200 if res.get("ok") else 503), res

  if action == "get_job":
    job_id = str(data.get("job_id") or data.get("id") or "").strip()
    if not job_id:
      return 400, {"ok": False, "error": "job_id is required"}
    try:
      res = asyncio.run(_kaira_request(root, action="get_job", job_id=job_id))
    except Exception as exc:
      return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return (200 if res.get("ok") else 404), res

  if action == "cancel_job":
    job_id = str(data.get("job_id") or data.get("id") or "").strip()
    if not job_id:
      return 400, {"ok": False, "error": "job_id is required"}
    try:
      res = asyncio.run(_kaira_request(root, action="cancel_job", job_id=job_id))
    except Exception as exc:
      return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return (200 if res.get("ok") else 400), res

  if action == "bus_publish":
    topic = str(data.get("topic") or "chat").strip()
    to_addr = str(data.get("to") or "").strip()
    payload = data.get("payload")
    if payload is None:
      payload = str(data.get("message") or data.get("text") or "")
    try:
      res = asyncio.run(
        _kaira_request(
          root,
          action="publish",
          topic=topic,
          to=to_addr,
          from_addr=str(data.get("from") or "gemcode-web"),
          payload=payload,
        )
      )
    except Exception as exc:
      return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return (200 if res.get("ok") else 503), res

  return 400, {
    "ok": False,
    "error": "action must be start, stop, status, inbox, list_jobs, get_job, cancel_job, or bus_publish",
  }
