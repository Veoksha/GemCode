from pathlib import Path

from gemcode.skills import (
  build_skill_invocation_prompt,
  discover_skill_metas,
  expand_skill_text,
  fuzzy_resolve_skill_name,
  load_skill,
  try_resolve_natural_language_skill,
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


def test_fuzzy_resolve_skill_name(tmp_path: Path, monkeypatch) -> None:
  home = tmp_path / "home"
  monkeypatch.setenv("HOME", str(home))
  _write(
    home / ".gemcode" / "skills" / "sandeep-docs" / "SKILL.md",
    "---\nname: sandeep-docs\ndescription: Sandeep universal document skill\n---\nBody\n",
  )
  assert fuzzy_resolve_skill_name(tmp_path, "Sandeep doc") == "sandeep-docs"
  assert fuzzy_resolve_skill_name(tmp_path, "sandeep-docs") == "sandeep-docs"


def test_try_resolve_natural_language_skill(tmp_path: Path, monkeypatch) -> None:
  home = tmp_path / "home"
  monkeypatch.setenv("HOME", str(home))
  _write(
    home / ".gemcode" / "skills" / "sandeep-docs" / "SKILL.md",
    "---\nname: sandeep-docs\ndescription: docs\n---\nBody\n",
  )
  resolved = try_resolve_natural_language_skill(
    tmp_path,
    "using the Sandeep doc skill create a doc on environment ghg",
  )
  assert resolved is not None
  assert resolved[0] == "sandeep-docs"


def test_build_skill_invocation_prompt_inline(tmp_path: Path) -> None:
  _write(
    tmp_path / ".gemcode" / "skills" / "writer" / "SKILL.md",
    "---\nname: writer\ndescription: Writes docs\n---\nWrite with $ARGUMENTS\n",
  )
  _write(tmp_path / ".gemcode" / "skills" / "writer" / "references" / "tpl.md", "template")
  prompt = build_skill_invocation_prompt(
    tmp_path,
    "writer",
    arguments="environment ghg",
    inline=True,
  )
  assert prompt is not None
  assert "ACTIVE SKILL" in prompt
  assert "environment ghg" in prompt
  assert "references/tpl.md" in prompt
  assert "template" in prompt
  assert "write_file" in prompt
  assert "do not improvise styling" in prompt


def test_builtin_batch_skill_available(tmp_path: Path) -> None:
  # Built-ins should be available even with no on-disk skills.
  metas = discover_skill_metas(tmp_path)
  assert "batch" in metas
  s = load_skill(tmp_path, "batch")
  assert s is not None
  assert s.meta.name == "batch"

