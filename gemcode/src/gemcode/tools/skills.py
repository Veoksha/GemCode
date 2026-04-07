from __future__ import annotations

from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.skills import build_skill_manifest_text, list_supporting_files, load_skill, expand_skill_text


def make_skill_tools(cfg: GemCodeConfig) -> list:
  def list_skills() -> dict[str, Any]:
    """
    List available GemSkills for this project.

    Skills live in:
    - `.gemcode/skills/<name>/SKILL.md` (project / monorepo)
    - `~/.gemcode/skills/<name>/SKILL.md` (personal)
    """
    from gemcode.skills import discover_skill_metas

    metas = discover_skill_metas(cfg.project_root)
    return {
      "skills": [
        {
          "name": m.name,
          "description": m.description,
          "disable_model_invocation": m.disable_model_invocation,
          "user_invocable": m.user_invocable,
        }
        for (m, _dir) in (metas[k] for k in sorted(metas.keys()))
      ]
    }

  def load_skill_tool(name: str, arguments: str = "", session_id: str | None = None) -> dict[str, Any]:
    """
    Load and expand a GemSkill's instructions for the given arguments.

    Returns:
    - expanded_text: markdown instructions with $ARGUMENTS substitutions applied
    - supporting_files: relative paths inside the skill directory (excluding SKILL.md)
    """
    s = load_skill(cfg.project_root, name)
    if s is None:
      return {"error": f"skill not found: {name}"}
    expanded = expand_skill_text(s, arguments=arguments or "", session_id=session_id)
    return {
      "name": s.meta.name,
      "description": s.meta.description,
      "expanded_text": expanded,
      "supporting_files": list_supporting_files(s),
      "skill_dir": str(s.skill_dir),
    }

  def skills_manifest() -> dict[str, Any]:
    """Return the skill manifest text injected into the agent instruction."""
    return {"text": build_skill_manifest_text(cfg.project_root)}

  list_skills.__name__ = "list_skills"
  load_skill_tool.__name__ = "load_skill"
  skills_manifest.__name__ = "skills_manifest"
  return [list_skills, load_skill_tool, skills_manifest]

