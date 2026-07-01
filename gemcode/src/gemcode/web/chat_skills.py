"""Resolve GemSkill invocations for the web chat SSE adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from gemcode.config import GemCodeConfig
from gemcode.repl_slash import ReplSlashResult, process_repl_slash
from gemcode.skills import (
  build_skill_invocation_prompt,
  discover_skill_metas,
  fuzzy_resolve_skill_name,
  try_resolve_natural_language_skill,
)
from gemcode.slash_commands import parse_slash_command


@dataclass
class WebChatInputResolution:
  """How the web chat should handle one user message."""

  prompt: str
  direct_response: str | None = None
  skill_name: str | None = None
  use_code_tools: bool = False
  force_rebuild_runner: bool = False


def _capture_print(lines: list[str]) -> Callable[..., None]:
  def _out(*args: Any, **kwargs: Any) -> None:
    end = kwargs.get("end", "\n")
    lines.append("".join(str(a) for a in args) + str(end))

  return _out


async def resolve_web_chat_input(
  cfg: GemCodeConfig,
  prompt: str,
  *,
  session_id: str,
  runner: Any,
) -> WebChatInputResolution:
  """
  Apply REPL slash handling and natural-language skill detection for web chat.

  Web chat always inline-expands skills so the model does not need `load_skill`
  (which is stripped in chat mode).
  """
  text = (prompt or "").strip()
  if not text:
    return WebChatInputResolution(prompt=prompt)

  lines: list[str] = []
  slash = await process_repl_slash(
    cfg=cfg,
    runner=runner,
    session_id=session_id,
    prompt_text=text,
    print_fn=_capture_print(lines),
  )

  if slash is not None:
    if slash.skip_model_turn:
      body = "".join(lines).strip()
      return WebChatInputResolution(
        prompt="",
        direct_response=body or "Done.",
        force_rebuild_runner=slash.force_rebuild_runner,
      )
    if slash.model_prompt:
      skill_name = _skill_name_from_slash(text, cfg.project_root)
      if skill_name:
        inline = build_skill_invocation_prompt(
          cfg.project_root,
          skill_name,
          arguments=_slash_arguments(text, skill_name),
          session_id=session_id,
          inline=True,
        )
        if inline:
          return WebChatInputResolution(
            prompt=inline,
            skill_name=skill_name,
            use_code_tools=True,
            force_rebuild_runner=slash.force_rebuild_runner,
          )
      return WebChatInputResolution(
        prompt=slash.model_prompt,
        force_rebuild_runner=slash.force_rebuild_runner,
      )

  resolved = try_resolve_natural_language_skill(cfg.project_root, text)
  if resolved:
    skill_name, task = resolved
    inline = build_skill_invocation_prompt(
      cfg.project_root,
      skill_name,
      arguments=task,
      session_id=session_id,
      inline=True,
    )
    if inline:
      return WebChatInputResolution(
        prompt=inline,
        skill_name=skill_name,
        use_code_tools=True,
      )

  return WebChatInputResolution(prompt=text)


def _skill_name_from_slash(prompt: str, project_root) -> str | None:
  sc = parse_slash_command(prompt)
  if sc is None:
    return None
  name = sc.command_name.lower()
  if name in ("skills", "skill"):
    parts = (sc.args or "").strip().split()
    return parts[0].strip().lower() if parts else None
  if name == "gemskill":
    parts = (sc.args or "").strip().split()
    return parts[0].strip().lower() if parts else None
  metas = discover_skill_metas(project_root)
  if name in metas:
    return name
  return fuzzy_resolve_skill_name(project_root, name)


def _slash_arguments(prompt: str, skill_name: str) -> str:
  sc = parse_slash_command(prompt)
  if sc is None:
    return prompt
  name = sc.command_name.lower()
  if name in ("skills", "skill"):
    parts = (sc.args or "").strip().split()
    if parts and parts[0].strip().lower() == skill_name:
      return " ".join(parts[1:]).strip()
    return (sc.args or "").strip()
  if name == skill_name:
    return (sc.args or "").strip()
  return prompt
