"""Tests for shared REPL slash dispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gemcode.config import GemCodeConfig
from gemcode.repl_slash import process_repl_slash
from gemcode.trust import is_trusted_root, trust_root


@pytest.mark.asyncio
async def test_process_repl_slash_none_for_plain_prompt() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  assert (
      await process_repl_slash(
          cfg=cfg,
          runner=object(),
          session_id="s",
          prompt_text="hello world",
      )
      is None
  )


@pytest.mark.asyncio
async def test_process_repl_slash_help() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  buf: list[str] = []

  def capture(*a: object, **_: object) -> None:
    if a:
      buf.append(str(a[0]))

  res = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/help",
      print_fn=capture,
  )
  assert res is not None
  assert res.skip_model_turn is True
  joined = "\n".join(buf)
  assert "Slash commands" in joined


@pytest.mark.asyncio
async def test_process_repl_slash_context_uses_session() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  sess = MagicMock()
  sess.state = {"gemcode:last_prompt_tokens": 100}
  runner = MagicMock()
  runner.session_service.get_session = AsyncMock(return_value=sess)

  res = await process_repl_slash(
      cfg=cfg,
      runner=runner,
      session_id="sid",
      prompt_text="/context",
  )
  assert res is not None
  assert res.skip_model_turn is True
  runner.session_service.get_session.assert_called_once()


@pytest.mark.asyncio
async def test_process_repl_slash_tools_passes_extra_tools() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  dummy = object()
  with patch("gemcode.tools_inspector.inspect_tools") as mock_inspect:
    mock_inspect.return_value = []
    res = await process_repl_slash(
        cfg=cfg,
        runner=object(),
        session_id="s",
        prompt_text="/tools",
        extra_tools=[dummy],
    )
  assert res is not None
  assert res.skip_model_turn is True
  mock_inspect.assert_called_once()
  assert mock_inspect.call_args[0][0] is cfg
  assert mock_inspect.call_args.kwargs.get("extra_tools") == [dummy]


@pytest.mark.asyncio
async def test_process_repl_slash_trust_on_off(tmp_path: Path, monkeypatch) -> None:
  """Use isolated GEMCODE_HOME so trust.json does not depend on ~/.gemcode in CI/sandbox."""
  gem_home = tmp_path / "gemhome"
  gem_home.mkdir()
  monkeypatch.setenv("GEMCODE_HOME", str(gem_home))

  cfg = GemCodeConfig(project_root=tmp_path)
  trust_root(tmp_path, trusted=False)
  assert not is_trusted_root(tmp_path)

  res_on = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/trust on",
      print_fn=lambda *_a, **_k: None,
  )
  assert res_on is not None and res_on.skip_model_turn is True
  assert is_trusted_root(cfg.project_root)

  res_off = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/trust off",
      print_fn=lambda *_a, **_k: None,
  )
  assert res_off is not None and res_off.skip_model_turn is True
  assert not is_trusted_root(cfg.project_root)


@pytest.mark.asyncio
async def test_process_repl_slash_eval_writes_record(tmp_path: Path, monkeypatch) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)

  def fake_run_eval_suite(**kwargs: object) -> dict:
    assert kwargs.get("session_cfg") is cfg
    return {
      "ok": True,
      "score": 1.0,
      "elapsed_s": 0.01,
      "results": [{"name": "tools_smoke", "ok": True, "details": ""}],
    }

  monkeypatch.setattr("gemcode.evals.harness.run_eval_suite", fake_run_eval_suite)

  res = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/eval",
      print_fn=lambda *_a, **_k: None,
  )
  assert res is not None and res.skip_model_turn is True
  out_path = tmp_path / ".gemcode" / "evals" / "last_eval.json"
  assert out_path.is_file()


@pytest.mark.asyncio
async def test_process_repl_slash_tools_smoke_ok(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  from gemcode.tools_inspector import ToolInspection

  with patch("gemcode.tools_inspector.inspect_tools") as mock_inspect:
    mock_inspect.return_value = [
        ToolInspection(
            name="dummy_tool",
            category="read_only",
            declaration_present=True,
            declaration_error=None,
            tool_type="callable",
        )
    ]
    buf: list[str] = []

    def capture(*a: object, **_k: object) -> None:
      if a:
        buf.append(str(a[0]))

    res = await process_repl_slash(
        cfg=cfg,
        runner=object(),
        session_id="s",
        prompt_text="/tools smoke",
        print_fn=capture,
    )
  assert res is not None and res.skip_model_turn is True
  joined = "\n".join(buf)
  assert "tools smoke: OK" in joined


@pytest.mark.asyncio
async def test_create_gemskill_scaffolds_skill(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  res = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/create gemskill alias-skill Short description here",
      print_fn=lambda *_a, **_k: None,
  )
  assert res is not None and res.skip_model_turn is True
  p = tmp_path / ".gemcode" / "skills" / "alias-skill" / "SKILL.md"
  assert p.is_file()
  text = p.read_text(encoding="utf-8")
  assert "alias-skill" in text
  assert "Short description here" in text


@pytest.mark.asyncio
async def test_gemskill_loads_into_session(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  skill_dir = tmp_path / ".gemcode" / "skills" / "my-skill"
  skill_dir.mkdir(parents=True)
  (skill_dir / "SKILL.md").write_text(
      "---\nname: my-skill\ndescription: test\n---\n\n# Body\n",
      encoding="utf-8",
  )
  res = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="sid-1",
      prompt_text="/gemskill my-skill",
      print_fn=lambda *_a, **_k: None,
  )
  assert res is not None and res.skip_model_turn is True
  assert res.force_rebuild_runner is True
  assert "my-skill" in cfg.session_loaded_skill_names
  assert cfg.session_skill_expand_session_id == "sid-1"


@pytest.mark.asyncio
async def test_agent_workspace_init_scaffolds_files(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  buf: list[str] = []

  def capture(*a: object, **_k: object) -> None:
    if a:
      buf.append(str(a[0]))

  # Create an agent first (this will create the workspace folder).
  res1 = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/agent create alpha",
      print_fn=capture,
  )
  assert res1 is not None and res1.skip_model_turn is True

  # Now run workspace init (should be idempotent and ensure workspace/*.md exist).
  res2 = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/agent workspace init alpha",
      print_fn=capture,
  )
  assert res2 is not None and res2.skip_model_turn is True

  # Find the created agent workspace by scanning `.gemcode/agents`.
  agents_dir = tmp_path / ".gemcode" / "agents"
  assert agents_dir.is_dir()
  agent_ws = next(iter(sorted([p for p in agents_dir.iterdir() if p.is_dir()])), None)
  assert agent_ws is not None
  wdir = agent_ws / "workspace"
  assert (wdir / "GOALS.md").is_file()
  assert (wdir / "POLICIES.md").is_file()
  assert (wdir / "SKILLS.md").is_file()
  assert (wdir / "HEARTBEAT.md").is_file()


@pytest.mark.asyncio
async def test_agent_commands_work_from_inside_agent_workspace(tmp_path: Path) -> None:
  # Create an agent at fleet root.
  fleet_cfg = GemCodeConfig(project_root=tmp_path)
  await process_repl_slash(
      cfg=fleet_cfg,
      runner=object(),
      session_id="s",
      prompt_text="/agent create alpha",
      print_fn=lambda *_a, **_k: None,
  )

  # Locate the created workspace.
  agents_dir = tmp_path / ".gemcode" / "agents"
  agent_ws = next(iter(sorted([p for p in agents_dir.iterdir() if p.is_dir()])), None)
  assert agent_ws is not None

  # Now run GemCode "from inside" that agent workspace and verify /agent list still
  # sees the fleet (shared org.json at the parent root).
  agent_cfg = GemCodeConfig(project_root=agent_ws)
  buf: list[str] = []

  def capture(*a: object, **_k: object) -> None:
    if a:
      buf.append(str(a[0]))

  res = await process_repl_slash(
      cfg=agent_cfg,
      runner=object(),
      session_id="s",
      prompt_text="/agent list",
      print_fn=capture,
  )
  assert res is not None and res.skip_model_turn is True
  joined = "\n".join(buf)
  assert "alpha" in joined


@pytest.mark.asyncio
async def test_append_gemskill_returns_model_prompt(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  skill_dir = tmp_path / ".gemcode" / "skills" / "x-skill"
  skill_dir.mkdir(parents=True)
  (skill_dir / "SKILL.md").write_text(
      "---\nname: x-skill\ndescription: x\n---\n\n# X\n",
      encoding="utf-8",
  )
  res = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/append gemskill x-skill add section Z",
      print_fn=lambda *_a, **_k: None,
  )
  assert res is not None
  assert res.skip_model_turn is False
  assert res.model_prompt and "iterate" in res.model_prompt.lower()
  assert "add section Z" in res.model_prompt
