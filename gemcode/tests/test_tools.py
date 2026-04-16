from pathlib import Path
import sys
from unittest.mock import MagicMock

from gemcode.hitl_session import HITL_STICKY_SESSION_KEY

from gemcode.config import GemCodeConfig
from gemcode.tools.edit import make_edit_tools
from gemcode.tools.filesystem import make_filesystem_tools
from gemcode.tools.shell import make_run_command
from gemcode.tools.veomem_tools import make_veomem_tools
from gemcode.tools.shell_gate import arm_confirmed_shell_basename
from gemcode.tools.todo import TODO_STATE_KEY, make_todo_tool
from gemcode.trust import trust_root


def test_default_max_llm_calls(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.delenv("GEMCODE_MAX_LLM_CALLS", raising=False)
  cfg = GemCodeConfig(project_root=tmp_path)
  assert cfg.max_llm_calls == 256


def test_todo_write_merge(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  tw = make_todo_tool(cfg)
  ctx = MagicMock()
  ctx.state = {}
  r = tw(False, [{"id": "a", "content": "one", "status": "pending"}], ctx)
  assert r.get("ok") is True
  assert len(r["todos"]) == 1
  r2 = tw(True, [{"id": "a", "content": "one", "status": "completed"}], ctx)
  assert r2["todos"][0]["status"] == "completed"
  assert len(ctx.state[TODO_STATE_KEY]) == 1


def test_read_file(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path / ".gemstate"))
  trust_root(tmp_path, trusted=True)
  cfg = GemCodeConfig(project_root=tmp_path)
  (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
  read_file, _, _, _, _ = make_filesystem_tools(cfg)
  out = read_file("x.txt")
  assert out["content"] == "hello"


def test_run_command_allowlist_bypass_after_shell_gate(tmp_path: Path, monkeypatch) -> None:
  """Interactive approval arms one shot: rm works without being on GEMCODE_ALLOW_COMMANDS."""
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path / ".gemstate"))
  trust_root(tmp_path, trusted=True)
  cfg = GemCodeConfig(project_root=tmp_path)
  assert "rm" not in cfg.allow_commands
  tgt = tmp_path / "x.txt"
  tgt.write_text("hi", encoding="utf-8")
  arm_confirmed_shell_basename("rm")
  run_command = make_run_command(cfg)
  out = run_command("rm", ["x.txt"])
  assert out.get("exit_code") == 0
  assert not tgt.exists()
  # Gate is not consumed when a different executable runs first (still non-allowlisted).
  arm_confirmed_shell_basename("rm")
  assert "uname" not in cfg.allow_commands
  wrong = run_command("uname", ["-a"])
  assert "not in allowlist" in str(wrong.get("error", ""))
  tgt2 = tmp_path / "y.txt"
  tgt2.write_text("z", encoding="utf-8")
  ok2 = run_command("rm", ["y.txt"])
  assert ok2.get("exit_code") == 0
  assert not tgt2.exists()


def test_run_command_sticky_session_bypasses_allowlist(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path / ".gemstate"))
  trust_root(tmp_path, trusted=True)
  cfg = GemCodeConfig(project_root=tmp_path)
  assert "uname" not in cfg.allow_commands
  run_command = make_run_command(cfg)
  ctx = MagicMock()
  ctx.state = {HITL_STICKY_SESSION_KEY: True}
  out = run_command("uname", ["-a"], tool_context=ctx)
  assert out.get("exit_code") == 0
  assert (out.get("stdout") or out.get("stderr") or "").strip()


def test_run_command_cwd_subdir(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path / ".gemstate"))
  trust_root(tmp_path, trusted=True)
  cfg = GemCodeConfig(project_root=tmp_path)
  nest = tmp_path / "nest"
  nest.mkdir()
  (nest / "marker.txt").write_text("in-nest", encoding="utf-8")
  run_command = make_run_command(cfg)
  ctx = MagicMock()
  ctx.state = {HITL_STICKY_SESSION_KEY: True}
  out = run_command(
      "python3",
      ["-c", "print(open('marker.txt').read())"],
      cwd_subdir="nest",
      tool_context=ctx,
  )
  assert out.get("exit_code") == 0
  assert "in-nest" in (out.get("stdout") or "")


def test_run_command_background_returns_pid(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path / ".gemstate"))
  trust_root(tmp_path, trusted=True)
  cfg = GemCodeConfig(project_root=tmp_path)
  run_command = make_run_command(cfg)
  ctx = MagicMock()
  ctx.state = {HITL_STICKY_SESSION_KEY: True}
  out = run_command(
      "python3",
      ["-c", "print(1)"],
      background=True,
      tool_context=ctx,
  )
  assert out.get("background") is True
  assert isinstance(out.get("pid"), int)


def test_run_command_extra_env_merges(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path / ".gemstate"))
  trust_root(tmp_path, trusted=True)
  cfg = GemCodeConfig(project_root=tmp_path)
  run_command = make_run_command(cfg)
  ctx = MagicMock()
  ctx.state = {HITL_STICKY_SESSION_KEY: True}
  out = run_command(
      "python3",
      ["-c", "import os; print(os.environ.get('GEMCODE_TEST_EXTRA', ''))"],
      extra_env_keys=["GEMCODE_TEST_EXTRA"],
      extra_env_values=["ok"],
      tool_context=ctx,
  )
  assert out.get("exit_code") == 0
  assert "ok" in (out.get("stdout") or "")


def test_delete_file(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path / ".gemstate"))
  trust_root(tmp_path, trusted=True)
  cfg = GemCodeConfig(project_root=tmp_path)
  p = tmp_path / "gone.txt"
  p.write_text("bye", encoding="utf-8")
  _, _, _, delete_file, _ = make_filesystem_tools(cfg)
  out = delete_file("gone.txt")
  assert out.get("deleted") is True
  assert not p.exists()


def test_search_replace(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
  _, search_replace = make_edit_tools(cfg)
  out = search_replace("f.py", "a = 1", "a = 2")
  assert "error" not in out
  assert (tmp_path / "f.py").read_text() == "a = 2\n"


def test_write_file_blocks_vendor_instruction_filenames(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  write_file, _ = make_edit_tools(cfg)
  for rel in ("CLAUDE.md", "docs/AGENTS.md", ".cursorrules", "claude.local.md"):
    out = write_file(rel, "# no\n")
    assert out.get("error_kind") == "blocked_special_file", rel
  assert not (tmp_path / "CLAUDE.md").exists()
  assert not (tmp_path / ".cursorrules").exists()
  assert not (tmp_path / "docs" / "AGENTS.md").exists()


def test_veomem_tool_wrappers_roundtrip(tmp_path: Path, monkeypatch) -> None:
  repo_root = Path(__file__).resolve().parents[2]
  veomem_src = repo_root / "veomem" / "src"
  p = str(veomem_src)
  if p not in sys.path:
    sys.path.insert(0, p)

  monkeypatch.setenv("GEMCODE_VEOMEM", "1")

  from veomem.store import add_observation, init_store

  init_store(tmp_path)
  a = add_observation(
    tmp_path,
    kind="tool",
    title="read_file",
    text="read src/app.py and noted auth workflow",
    session_id="s1",
    tool_name="read_file",
    wing="w1",
    room="auth",
    paths=["src/app.py"],
    extra={"primary_path": "src/app.py"},
    ts_epoch_ms=1_000,
  )
  b = add_observation(
    tmp_path,
    kind="tool",
    title="write_file",
    text="updated src/app.py to fix auth workflow",
    session_id="s1",
    tool_name="write_file",
    wing="w1",
    room="auth",
    paths=["src/app.py"],
    extra={"primary_path": "src/app.py"},
    ts_epoch_ms=40_000,
  )

  cfg = GemCodeConfig(project_root=tmp_path)
  tools = {t.__name__: t for t in make_veomem_tools(cfg)}
  assert {"veomem_search", "veomem_timeline", "veomem_get_observations"} <= set(tools)

  res = tools["veomem_search"]("auth workflow", limit=5)
  assert res.get("ok") is True
  ids = [int(x["id"]) for x in res.get("results") or []]
  assert int(a["id"]) in ids or int(b["id"]) in ids

  tl = tools["veomem_timeline"](int(b["id"]), window_ms=60_000, limit=10)
  assert tl.get("ok") is True
  tl_ids = [int(x["id"]) for x in tl.get("results") or []]
  assert int(a["id"]) in tl_ids
  assert int(b["id"]) in tl_ids

  full = tools["veomem_get_observations"](f"{a['id']},{b['id']}", max_chars=500)
  assert full.get("ok") is True
  assert full.get("count") == 2
  texts = " ".join(str(x.get("text") or "") for x in full.get("results") or [])
  assert "auth workflow" in texts
