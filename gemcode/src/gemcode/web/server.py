"""Built-in HTTP API for GemCode web UIs — ``gemcode serve``."""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from gemcode.web.serve_bind import bind_http_server, format_connection_lines
from gemcode.web.serve_state import (
  clear_serve_state,
  serve_base_url,
  write_serve_state,
)

PYTHON = sys.executable
SERVER_VERSION = "GemCodeServe/1.0"

# SSE / long-turn limits (0 = no server-side turn cap).
_SSE_KEEPALIVE_S = float(os.environ.get("GEMCODE_WEB_SSE_KEEPALIVE_S", "20"))
_TURN_TIMEOUT_S = float(os.environ.get("GEMCODE_WEB_TURN_TIMEOUT_S", "0"))


def _mock_allowed() -> bool:
  return os.environ.get("GEMCODE_WEB_ALLOW_MOCK", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
  )


def _mock_mode_active() -> bool:
  if not _mock_allowed():
    return False
  return bool(os.environ.get("GEMCODE_WEB_MOCK_RESPONSE", "").strip())


def _strip_mock_unless_allowed(env: dict[str, str]) -> dict[str, str]:
  if not _mock_allowed():
    env.pop("GEMCODE_WEB_MOCK_RESPONSE", None)
    env.pop("GEMCODE_WEB_MOCK_CHUNK", None)
  return env


def _gemcode_env(project_root: str) -> dict[str, str]:
  env = os.environ.copy()
  env["GEMCODE_WEB_PROJECT_ROOT"] = project_root
  if not env.get("GOOGLE_API_KEY") and env.get("GEMINI_API_KEY"):
    env["GOOGLE_API_KEY"] = env["GEMINI_API_KEY"]
  try:
    from gemcode.credentials import apply_saved_google_api_key_to_environ

    apply_saved_google_api_key_to_environ()
    if os.environ.get("GOOGLE_API_KEY"):
      env["GOOGLE_API_KEY"] = os.environ["GOOGLE_API_KEY"]
  except Exception:
    pass
  return _strip_mock_unless_allowed(env)


def _pick_folder_native() -> dict[str, Any]:
  """Blocking OS folder dialog. Returns {cancelled}, {path}, or {error}."""
  system = platform.system()

  if system == "Darwin":
    script = (
      'set theFolder to choose folder with prompt "Select project folder"\n'
      "return POSIX path of theFolder"
    )
    try:
      proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=600,
      )
    except (OSError, subprocess.TimeoutExpired) as exc:
      return {"error": str(exc)}
    if proc.returncode != 0:
      err = (proc.stderr or proc.stdout or "").strip().lower()
      if "cancel" in err or proc.returncode == 1:
        return {"cancelled": True}
      return {"error": (proc.stderr or proc.stdout or "Folder picker failed").strip()}
    path = (proc.stdout or "").strip()
    if not path:
      return {"cancelled": True}
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
      return {"error": "Selected path is not a folder"}
    return {"path": str(resolved)}

  if system == "Linux":
    try:
      proc = subprocess.run(
        ["zenity", "--file-selection", "--directory", "--title=Select project folder"],
        capture_output=True,
        text=True,
        timeout=600,
      )
      if proc.returncode == 1:
        return {"cancelled": True}
      if proc.returncode != 0:
        return {"error": (proc.stderr or "Folder picker failed").strip()}
      path = (proc.stdout or "").strip()
      if not path:
        return {"cancelled": True}
      resolved = Path(path).expanduser().resolve()
      if not resolved.is_dir():
        return {"error": "Selected path is not a folder"}
      return {"path": str(resolved)}
    except FileNotFoundError:
      pass

  try:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
      root.attributes("-topmost", True)
    except Exception:
      pass
    path = filedialog.askdirectory(title="Select project folder")
    root.destroy()
    if not path:
      return {"cancelled": True}
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
      return {"error": "Selected path is not a folder"}
    return {"path": str(resolved)}
  except Exception as exc:
    return {"error": f"Native folder picker unavailable: {exc}"}


def _build_handler(project_root: str) -> type[BaseHTTPRequestHandler]:
  root = str(Path(project_root).expanduser().resolve())

  class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION

    def log_message(self, fmt: str, *args: Any) -> None:
      sys.stderr.write(f"[serve] {self.address_string()} - {fmt % args}\n")

    def _cors(self) -> None:
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
      self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
      self.send_response(204)
      self._cors()
      self.end_headers()

    def do_HEAD(self) -> None:
      if self.path in ("/api/health", "/api/status", "/api/chat"):
        self.send_response(200)
        self._cors()
        self.end_headers()
        return
      self.send_error(404)

    def do_GET(self) -> None:
      if self.path in ("/api/health", "/api/status"):
        try:
          from gemcode.credentials import apply_saved_google_api_key_to_environ

          apply_saved_google_api_key_to_environ()
        except Exception:
          pass
        has_key = bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        body = json.dumps(
          {
            "status": "ok",
            "service": "gemcode-serve",
            "gemcode": True,
            "has_api_key": has_key,
            "mock_mode": _mock_mode_active(),
            "project_root": root,
            "cwd": root,
            "port": self.server.server_address[1],
            "url": serve_base_url(self.server.server_address[0], self.server.server_address[1]),
          }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return
      if self.path == "/api/session":
        body = json.dumps(
          {
            "cwd": root,
            "env": {"GEMCODE_WEB_PROJECT_ROOT": root},
            "version": SERVER_VERSION,
            "gemcode": True,
            "has_api_key": bool(
              os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
            ),
            "mock_mode": _mock_mode_active(),
          }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return
      if self.path.startswith("/api/workspace/validate"):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        raw_path = (params.get("path") or [""])[0]
        try:
          resolved = Path(raw_path).expanduser().resolve()
          if resolved.is_dir():
            payload = {"valid": True, "resolved": str(resolved)}
          else:
            payload = {"valid": False, "error": "Folder not found"}
        except (OSError, ValueError) as exc:
          payload = {"valid": False, "error": str(exc)}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return

      parsed = urlparse(self.path)
      fleet_get = {
        "/api/org": "org",
        "/api/habits": "habits",
        "/api/mesh/status": "mesh",
        "/api/skills": "skills",
        "/api/mcp": "mcp",
        "/api/config": "config",
        "/api/runtime/status": "runtime_status",
        "/api/runtime/inbox": "runtime_inbox",
        "/api/sessions": "sessions",
        "/api/panel": "panel",
        "/api/preview": "preview",
      }.get(parsed.path)
      if fleet_get:
        params = parse_qs(parsed.query)
        raw_path = (params.get("path") or [root])[0]
        skill_name = (params.get("name") or [None])[0]
        try:
          if fleet_get == "config":
            from gemcode.web.web_config_api import handle_config_get

            status, payload = handle_config_get(raw_path)
          elif fleet_get in ("runtime_status", "runtime_inbox"):
            from gemcode.web.runtime_api import handle_runtime_get

            kind = "status" if fleet_get == "runtime_status" else "inbox"
            status, payload = handle_runtime_get(kind, raw_path)
          elif fleet_get in ("skills", "mcp"):
            from gemcode.web.customize_api import handle_customize_get

            status, payload = handle_customize_get(fleet_get, raw_path, skill_name=skill_name)
          elif fleet_get == "sessions":
            from gemcode.web.sessions_api import handle_sessions_get

            status, payload = handle_sessions_get(raw_path)
          elif fleet_get == "panel":
            params = parse_qs(parsed.query)
            kind = (params.get("kind") or [""])[0]
            session_id = (params.get("session_id") or [None])[0]
            try:
              tail = int((params.get("tail") or ["40"])[0])
            except ValueError:
              tail = 40
            from gemcode.web.workspace_panel_api import (
              handle_panel_get,
              handle_panel_get_async,
            )

            if kind == "context" and session_id:
              from asyncio import run

              status, payload = run(
                handle_panel_get_async(kind, raw_path, session_id=session_id)
              )
            else:
              status, payload = handle_panel_get(
                kind, raw_path, session_id=session_id, tail=tail
              )
          elif fleet_get == "preview":
            from gemcode.web.preview_api import handle_preview_get

            status, payload = handle_preview_get("ports")
          else:
            from gemcode.web.fleet_api import handle_fleet_get

            status, payload = handle_fleet_get(fleet_get, raw_path)
        except Exception as exc:
          status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return

      self.send_error(404)

    def do_POST(self) -> None:
      if self.path == "/api/workspace/pick":
        payload = _pick_folder_native()
        body = json.dumps(payload).encode()
        status = 200 if "error" not in payload else 503
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return

      post_routes: dict[str, Any] = {
        "/api/habits": ("gemcode.web.fleet_api", "handle_habits_post"),
        "/api/mesh": ("gemcode.web.fleet_api", "handle_mesh_post"),
        "/api/org": ("gemcode.web.fleet_api", "handle_org_post"),
        "/api/skills": ("gemcode.web.customize_api", "handle_skills_post"),
        "/api/mcp": ("gemcode.web.customize_api", "handle_mcp_post"),
        "/api/panel": ("gemcode.web.workspace_panel_api", "handle_panel_post"),
        "/api/sessions": ("gemcode.web.sessions_api", "handle_sessions_post"),
        "/api/settings/credentials": ("gemcode.web.customize_api", "handle_credentials_post"),
        "/api/runtime": ("gemcode.web.runtime_api", "handle_runtime_post"),
        "/api/terminal": ("gemcode.web.terminal_api", "handle_terminal_post"),
      }

      if self.path in post_routes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
          data = json.loads(raw)
        except json.JSONDecodeError:
          self.send_error(400, "Invalid JSON body")
          return
        mod_name, fn_name = post_routes[self.path]
        import importlib

        mod = importlib.import_module(mod_name)
        handler = getattr(mod, fn_name)
        try:
          if self.path == "/api/settings/credentials":
            status, payload = handler(data)
          elif self.path == "/api/sessions":
            raw_path = str(data.get("path") or root).strip()
            status, payload = handler(raw_path, data)
          else:
            raw_path = str(data.get("path") or root).strip()
            status, payload = handler(data, raw_path)
        except Exception as exc:
          payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
          status = 500
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return

      if self.path == "/api/chat/approve":
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
          data = json.loads(raw)
        except json.JSONDecodeError:
          self.send_error(400, "Invalid JSON body")
          return
        try:
          from gemcode.web.hitl_bridge import resolve_web_approval

          approval_id = str(data.get("approval_id") or "").strip()
          confirmed = bool(data.get("confirmed"))
          payload = resolve_web_approval(approval_id, confirmed=confirmed)
          status = 200 if payload.get("ok") else 400
        except Exception as exc:
          payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
          status = 500
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return

      if self.path != "/api/chat":
        self.send_error(404)
        return

      length = int(self.headers.get("Content-Length", "0") or "0")
      raw = self.rfile.read(length) if length else b"{}"
      try:
        json.loads(raw)
      except json.JSONDecodeError:
        self.send_error(400, "Invalid JSON body")
        return

      proc = subprocess.Popen(
        [PYTHON, "-u", "-m", "gemcode.web.sse_adapter"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_gemcode_env(root),
      )
      assert proc.stdin is not None
      assert proc.stdout is not None
      proc.stdin.write(raw)
      proc.stdin.close()

      def _drain_stderr() -> None:
        try:
          if proc.stderr is not None:
            proc.stderr.read()
        except Exception:
          pass

      threading.Thread(target=_drain_stderr, daemon=True).start()

      self.send_response(200)
      self.send_header("Content-Type", "text/event-stream; charset=utf-8")
      self.send_header("Cache-Control", "no-cache, no-transform")
      self.send_header("Connection", "keep-alive")
      self.send_header("X-Accel-Buffering", "no")
      self._cors()
      self.end_headers()

      try:
        self.connection.settimeout(None)
      except Exception:
        pass

      out_q: queue.Queue[bytes | None] = queue.Queue()

      def _stdout_reader() -> None:
        try:
          while proc.stdout is not None:
            chunk = proc.stdout.read(4096)
            if not chunk:
              break
            out_q.put(chunk)
        except Exception:
          pass
        finally:
          out_q.put(None)

      threading.Thread(target=_stdout_reader, daemon=True).start()

      done_re = re.compile(rb'"type"\s*:\s*"done"')
      saw_done = False
      carry = b""
      last_activity = time.monotonic()
      turn_deadline = (
        time.monotonic() + _TURN_TIMEOUT_S if _TURN_TIMEOUT_S > 0 else None
      )
      try:
        while True:
          if turn_deadline is not None and time.monotonic() > turn_deadline:
            proc.kill()
            if not saw_done:
              payload = json.dumps(
                {
                  "type": "error",
                  "error": (
                    f"Turn exceeded server limit ({int(_TURN_TIMEOUT_S)}s). "
                    "Set GEMCODE_WEB_TURN_TIMEOUT_S=0 for no cap."
                  ),
                }
              )
              self.wfile.write(f"data: {payload}\n\n".encode())
              self.wfile.write(b'data: {"type": "done"}\n\n')
              self.wfile.flush()
            break
          try:
            chunk = out_q.get(timeout=5.0)
          except queue.Empty:
            if time.monotonic() - last_activity >= _SSE_KEEPALIVE_S:
              self.wfile.write(b": keepalive\n\n")
              self.wfile.flush()
              last_activity = time.monotonic()
            continue
          if chunk is None:
            break
          window = carry + chunk
          if not saw_done and done_re.search(window):
            saw_done = True
          carry = chunk[-64:]
          self.wfile.write(chunk)
          self.wfile.flush()
          last_activity = time.monotonic()
      except (BrokenPipeError, ConnectionResetError):
        proc.kill()
      finally:
        try:
          if _TURN_TIMEOUT_S > 0:
            proc.wait(timeout=max(30.0, _TURN_TIMEOUT_S))
          else:
            proc.wait()
        except subprocess.TimeoutExpired:
          proc.kill()
          proc.wait(timeout=5)
        err = ""
        try:
          if proc.stderr is not None:
            err = proc.stderr.read().decode("utf-8", errors="replace")
        except Exception:
          pass
        if proc.returncode != 0 and not saw_done:
          payload = json.dumps(
            {
              "type": "error",
              "error": (
                err.strip()[:500]
                if err.strip()
                else f"GemCode exited with code {proc.returncode}"
              ),
            }
          )
          try:
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.write(b'data: {"type": "done"}\n\n')
            self.wfile.flush()
          except Exception:
            pass
          if err:
            sys.stderr.write(err)

  return Handler


def _load_project_dotenv(project_root: Path) -> None:
  dotenv_path = project_root / ".env"
  if not dotenv_path.is_file():
    return
  try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path)
  except ImportError:
    pass


def run_server(
  *,
  project_root: Path | str,
  host: str = "127.0.0.1",
  port: int | None = None,
  session_id: str | None = None,
) -> None:
  """Start the GemCode HTTP API (blocks until interrupted)."""
  root = Path(project_root).expanduser().resolve()
  if not root.is_dir():
    raise SystemExit(f"Project root is not a directory: {root}")

  bind_port = port if port is not None else int(os.environ.get("GEMCODE_WEB_API_PORT", "3001"))
  os.environ["GEMCODE_WEB_PROJECT_ROOT"] = str(root)
  _load_project_dotenv(root)

  try:
    from gemcode.config import load_cli_environment

    load_cli_environment()
  except Exception:
    pass

  if os.environ.get("GEMCODE_WEB_MOCK_RESPONSE") and not _mock_allowed():
    print(
      "[serve] GEMCODE_WEB_MOCK_RESPONSE is set but ignored. "
      "Unset it or set GEMCODE_WEB_ALLOW_MOCK=1 to use mock replies.",
      flush=True,
    )
    os.environ.pop("GEMCODE_WEB_MOCK_RESPONSE", None)
    os.environ.pop("GEMCODE_WEB_MOCK_CHUNK", None)

  try:
    from gemcode.web.fleet_api import ensure_web_mesh_running

    mesh_boot = ensure_web_mesh_running(root)
    if mesh_boot.get("thread_running"):
      print("[serve] Mesh scheduler started for scheduled tasks", flush=True)
    elif mesh_boot.get("error"):
      print(f"[serve] Mesh scheduler not started: {mesh_boot['error']}", flush=True)
  except Exception as exc:
    print(f"[serve] Mesh scheduler bootstrap failed: {exc}", flush=True)

  handler = _build_handler(str(root))
  preferred_port = bind_port
  httpd, bind_port = bind_http_server(host, handler, preferred_port)
  url = serve_base_url(host, bind_port)

  def _shutdown(*_args: Any) -> None:
    try:
      httpd.shutdown()
    except Exception:
      pass
    clear_serve_state(root)

  signal.signal(signal.SIGINT, _shutdown)
  signal.signal(signal.SIGTERM, _shutdown)

  write_serve_state(
    root,
    {
      "pid": os.getpid(),
      "host": host,
      "port": bind_port,
      "url": url,
      "project_root": str(root),
      "session_id": session_id,
      "foreground": True,
    },
  )

  print(f"GemCode serve listening on {url}", flush=True)
  print(f"Project root: {root}", flush=True)
  if session_id:
    print(f"CLI session id: {session_id}", flush=True)
  for line in format_connection_lines(host, bind_port, preferred_port=preferred_port):
    print(line, flush=True)
  if _TURN_TIMEOUT_S <= 0:
    print("[serve] Long turns: no server-side turn timeout (GEMCODE_WEB_TURN_TIMEOUT_S=0)", flush=True)
  else:
    print(f"[serve] Long turns: server cap {int(_TURN_TIMEOUT_S)}s (set GEMCODE_WEB_TURN_TIMEOUT_S=0 to disable)", flush=True)
  if _mock_mode_active():
    print("[serve] WARNING: mock mode active (GEMCODE_WEB_ALLOW_MOCK=1)", flush=True)
  else:
    print("[serve] GemCode agent mode (real LLM + tools)", flush=True)

  try:
    httpd.serve_forever()
  finally:
    clear_serve_state(root)


def main(argv: list[str] | None = None) -> None:
  parser = argparse.ArgumentParser(prog="gemcode serve")
  parser.add_argument(
    "-C",
    "--directory",
    type=Path,
    default=Path.cwd(),
    help="Project root (default: current directory)",
  )
  parser.add_argument(
    "--host",
    default=os.environ.get("GEMCODE_WEB_API_HOST", "127.0.0.1"),
    help="Bind host (default: 127.0.0.1)",
  )
  parser.add_argument(
    "--port",
    type=int,
    default=int(os.environ.get("GEMCODE_WEB_API_PORT", "3001")),
    help="Bind port (default: 3001)",
  )
  parser.add_argument(
    "--session-id",
    default=None,
    help="Optional CLI session id to advertise to connected UIs",
  )
  args = parser.parse_args(argv)
  run_server(
    project_root=args.directory,
    host=args.host,
    port=args.port,
    session_id=args.session_id,
  )


if __name__ == "__main__":
  main()
