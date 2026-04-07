from pathlib import Path

from gemcode.skills import (
  discover_skill_metas,
  expand_skill_text,
  load_skill,
)


def _write(p: Path, text: str) -> None:
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(text, encoding="utf-8")


def test_skill_discovery_project_over_personal(tmp_path: Path, monkeypatch) -> None:
  # personal
  home = tmp_path / "home"
  monkeypatch.setenv("HOME", str(home))
  _write(
    home / ".gemcode" / "skills" / "hello" / "SKILL.md",
    "---\nname: hello\ndescription: personal\n---\nPersonal body\n",
  )
  # project overrides
  _write(
    tmp_path / ".gemcode" / "skills" / "hello" / "SKILL.md",
    "---\nname: hello\ndescription: project\n---\nProject body\n",
  )

  metas = discover_skill_metas(tmp_path)
  assert "hello" in metas
  meta, skill_dir = metas["hello"]
  assert meta.description == "project"
  assert str(skill_dir).endswith(".gemcode/skills/hello")


def test_skill_expand_arguments_substitution(tmp_path: Path) -> None:
  _write(
    tmp_path / ".gemcode" / "skills" / "deploy" / "SKILL.md",
    "---\nname: deploy\ndescription: Deploys\n---\nRun $0 then $ARGUMENTS[1] in $ARGUMENTS\n",
  )
  s = load_skill(tmp_path, "deploy")
  assert s is not None
  expanded = expand_skill_text(s, arguments="prod us-east", session_id="sess1")
  assert "Run prod then us-east" in expanded
  assert "prod us-east" in expanded


def test_skill_no_arguments_appends(tmp_path: Path) -> None:
  _write(
    tmp_path / ".gemcode" / "skills" / "x" / "SKILL.md",
    "---\nname: x\ndescription: X\n---\nDo the thing.\n",
  )
  s = load_skill(tmp_path, "x")
  assert s is not None
  expanded = expand_skill_text(s, arguments="", session_id=None)
  assert expanded.startswith("Do the thing.")


def test_builtin_batch_skill_available(tmp_path: Path) -> None:
  # Built-ins should be available even with no on-disk skills.
  metas = discover_skill_metas(tmp_path)
  assert "batch" in metas
  s = load_skill(tmp_path, "batch")
  assert s is not None
  assert s.meta.name == "batch"

