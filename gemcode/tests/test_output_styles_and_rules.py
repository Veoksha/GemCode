from pathlib import Path

from gemcode.output_styles import discover_output_styles, load_output_style
from gemcode.rules import load_rules


def _write(p: Path, text: str) -> None:
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(text, encoding="utf-8")


def test_output_style_project_overrides_personal(tmp_path: Path, monkeypatch) -> None:
  home = tmp_path / "home"
  monkeypatch.setenv("HOME", str(home))

  _write(home / ".gemcode" / "output-styles" / "brief.md", "personal")
  _write(tmp_path / ".gemcode" / "output-styles" / "brief.md", "project")

  styles = discover_output_styles(tmp_path)
  assert "brief" in styles
  s = load_output_style(tmp_path, "brief")
  assert s is not None
  assert s.text == "project"


def test_rules_path_gating(tmp_path: Path) -> None:
  _write(
    tmp_path / ".gemcode" / "rules" / "frontend.md",
    "---\nname: frontend\npaths: src/frontend/**\n---\nUse React.\n",
  )
  _write(
    tmp_path / ".gemcode" / "rules" / "general.md",
    "Always run tests.\n",
  )

  # No touched paths -> only ungated rule loads
  rules0 = load_rules(tmp_path, touched_paths=None)
  names0 = {r.name for r in rules0}
  assert "general" in names0
  assert "frontend" not in names0

  # With touched path -> gated rule loads
  rules1 = load_rules(tmp_path, touched_paths=["src/frontend/app.tsx"])
  names1 = {r.name for r in rules1}
  assert "general" in names1
  assert "frontend" in names1

