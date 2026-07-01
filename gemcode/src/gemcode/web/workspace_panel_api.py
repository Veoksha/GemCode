"""Web API for CLI parity panels: diagnostics, checkpoints, notes, trust, etc."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.web.project_root import resolve_web_project_root


def _cfg(raw_path: str | None) -> tuple[Path, GemCodeConfig]:
  root = resolve_web_project_root(raw_path)
  if not root.is_dir():
    raise ValueError(f"Invalid project root: {root}")
  from gemcode.org import resolve_fleet_root

  fleet = resolve_fleet_root(root)
  return fleet, GemCodeConfig(project_root=fleet)


def handle_panel_get(
  kind: str,
  raw_path: str | None,
  *,
  session_id: str | None = None,
  tail: int = 40,
) -> tuple[int, dict[str, Any]]:
  k = (kind or "").strip().lower()
  try:
    root, cfg = _cfg(raw_path)
  except ValueError as exc:
    return 400, {"ok": False, "error": str(exc)}

  try:
    if k == "doctor":
      from gemcode.repl_commands import format_doctor_lines

      lines = format_doctor_lines(cfg)
      return 200, {"ok": True, "kind": k, "lines": lines, "text": "\n".join(lines)}

    if k == "status":
      lines = _format_status_lines(cfg, session_id=session_id)
      return 200, {"ok": True, "kind": k, "lines": lines, "text": "\n".join(lines)}

    if k == "hooks":
      from gemcode.repl_commands import format_hooks_lines

      lines = format_hooks_lines(cfg)
      return 200, {"ok": True, "kind": k, "lines": lines, "text": "\n".join(lines)}

    if k == "audit":
      from gemcode.repl_commands import format_audit_lines

      lines = format_audit_lines(cfg, tail=max(1, min(int(tail), 500)))
      return 200, {"ok": True, "kind": k, "lines": lines, "text": "\n".join(lines)}

    if k == "notes":
      p = root / ".gemcode" / "notes.md"
      content = p.read_text(encoding="utf-8") if p.is_file() else ""
      return 200, {"ok": True, "kind": k, "content": content, "path": str(p)}

    if k == "trust":
      from gemcode.trust import is_trusted_root, trust_json_path

      return 200, {
        "ok": True,
        "kind": k,
        "trusted": is_trusted_root(root),
        "path": str(root),
        "trust_file": str(trust_json_path()),
      }

    if k == "checkpoints":
      from gemcode.checkpoints import list_checkpoints

      return 200, {
        "ok": True,
        "kind": k,
        "checkpoints": list_checkpoints(root, limit=30),
      }

    if k == "automations":
      from gemcode.automations import load_automation_state, load_automations

      autos = load_automations(root)
      state = load_automation_state(root)
      items = [
        {
          "name": a.name,
          "enabled": a.enabled,
          "priority": a.priority,
          "session_id": a.session_id,
          "prompt": a.prompt[:200] + ("…" if len(a.prompt) > 200 else ""),
          "triggers": [t.key() for t in (a.triggers or ())],
          "last_run": state.get(a.name),
        }
        for a in autos
      ]
      return 200, {"ok": True, "kind": k, "automations": items}

    if k == "openapi":
      specs = _list_openapi_specs(root)
      return 200, {"ok": True, "kind": k, "specs": specs}

    if k == "eval-last":
      p = root / ".gemcode" / "evals" / "last_eval.json"
      if not p.is_file():
        return 200, {"ok": True, "kind": k, "result": None}
      try:
        result = json.loads(p.read_text(encoding="utf-8"))
      except Exception:
        result = None
      return 200, {"ok": True, "kind": k, "result": result}

    if k == "fleet-inbox":
      from gemcode.fleet_reports import preview_fleet_inbox

      preview = preview_fleet_inbox(root, max_chars=12000)
      return 200, {"ok": True, "kind": k, "preview": preview}

    if k == "cost":
      stats = getattr(cfg, "_last_turn_stats", None)
      lines = _format_cost_lines(stats)
      return 200, {
        "ok": True,
        "kind": k,
        "stats": stats if isinstance(stats, dict) else None,
        "lines": lines,
        "text": "\n".join(lines),
      }

    if k == "context":
      return 200, {
        "ok": True,
        "kind": k,
        "session_id": session_id,
        "note": "Use GET with session_id after at least one chat turn for live token stats.",
        "lines": _format_context_static(cfg),
        "text": "\n".join(_format_context_static(cfg)),
      }

    return 400, {"ok": False, "error": f"Unknown panel kind: {kind}"}
  except Exception as exc:
    return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def handle_panel_get_async(
  kind: str,
  raw_path: str | None,
  *,
  session_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
  """Async panel kinds that need ADK session access."""
  k = (kind or "").strip().lower()
  if k != "context":
    return handle_panel_get(kind, raw_path, session_id=session_id)

  try:
    root, cfg = _cfg(raw_path)
  except ValueError as exc:
    return 400, {"ok": False, "error": str(exc)}

  if not session_id:
    lines = _format_context_static(cfg)
    lines.append("session_id: (not provided — pass active chat session_id)")
    return 200, {"ok": True, "kind": k, "lines": lines, "text": "\n".join(lines)}

  try:
    from gemcode.context_warning import (
      calculate_context_warning_state,
      get_auto_compact_threshold_tokens,
      get_effective_context_window_size_tokens,
    )
    from gemcode.session_runtime import create_runner

    runner = create_runner(cfg, extra_tools=None)
    try:
      sess = await runner.session_service.get_session(
        app_name="gemcode",
        user_id="local",
        session_id=session_id,
      )
    finally:
      try:
        close = runner.close()
        if hasattr(close, "__await__"):
          await close
      except Exception:
        pass

    st = getattr(sess, "state", None) or {}
    last_pt = st.get("gemcode:last_prompt_tokens")
    last_pct = st.get("gemcode:last_context_percent_left")
    eff = get_effective_context_window_size_tokens(cfg.model)
    aut = get_auto_compact_threshold_tokens(cfg.model)
    lines = [
      f"model: {cfg.model}",
      f"effective_context_window_tokens≈{eff}",
      f"autocompact_threshold_tokens≈{aut}",
      f"session_id: {session_id}",
    ]
    if isinstance(last_pt, int):
      cw = calculate_context_warning_state(
        prompt_token_count=last_pt, model=cfg.model, cfg=cfg
      )
      lines.append(f"last_prompt_token_count: {last_pt}")
      lines.append(f"estimated_percent_left: {cw.get('percent_left')}%")
      lines.append(
        "flags: "
        f"warning={cw.get('is_above_warning_threshold')} "
        f"error={cw.get('is_above_error_threshold')} "
        f"autocompact_zone={cw.get('is_above_auto_compact_threshold')} "
        f"blocking={cw.get('is_at_blocking_limit')}"
      )
    else:
      lines.append("last_prompt_token_count: (send a message in this session first)")
      if last_pct is not None:
        lines.append(f"last_stored_percent_left: {last_pct}")
    return 200, {"ok": True, "kind": k, "lines": lines, "text": "\n".join(lines)}
  except Exception as exc:
    return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def handle_panel_post(data: dict[str, Any], raw_path: str | None) -> tuple[int, dict[str, Any]]:
  action = str(data.get("action") or "").strip().lower()
  try:
    root, cfg = _cfg(raw_path or data.get("path"))
  except ValueError as exc:
    return 400, {"ok": False, "error": str(exc)}

  try:
    if action == "trust":
      from gemcode.trust import is_trusted_root, trust_root

      trusted = bool(data.get("trusted"))
      trust_root(root, trusted=trusted)
      return 200, {"ok": True, "trusted": is_trusted_root(root), "path": str(root)}

    if action == "checkpoint_restore":
      from gemcode.checkpoints import undo_checkpoint

      cp_id = str(data.get("checkpoint_id") or "").strip()
      result = undo_checkpoint(root, cp_id)
      if result.get("error"):
        return 400, {"ok": False, **result}
      return 200, {"ok": True, **result}

    if action == "notes_save":
      content = str(data.get("content") if data.get("content") is not None else "")
      p = root / ".gemcode" / "notes.md"
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_text(content, encoding="utf-8")
      return 200, {"ok": True, "path": str(p)}

    if action == "notes_clear":
      p = root / ".gemcode" / "notes.md"
      if p.is_file():
        p.unlink()
      return 200, {"ok": True}

    if action == "notes_append":
      note = str(data.get("note") or "").strip()
      if not note:
        return 400, {"ok": False, "error": "note is required"}
      from gemcode.tools.notes import build_notes_tools

      tools = build_notes_tools(root)
      append_fn = next(t for t in tools if getattr(t, "__name__", "") == "append_project_note")
      result = append_fn(note)
      return 200, {"ok": True, "result": result}

    if action == "automation_init":
      from gemcode.tools.automations_tools import make_automations_tools

      tools = {getattr(t, "__name__", ""): t for t in make_automations_tools(cfg)}
      init_fn = tools.get("automations_init")
      if not init_fn:
        return 500, {"ok": False, "error": "automations_init unavailable"}
      result = init_fn(
        str(data.get("name") or ""),
        prompt=str(data.get("prompt") or ""),
        enabled=bool(data.get("enabled", True)),
        trigger_kind=str(data.get("trigger_kind") or "nightly"),
        at_hhmm=str(data.get("at_hhmm") or "02:00"),
        every_seconds=int(data.get("every_seconds") or 0),
        cron=str(data.get("cron") or ""),
        overwrite=bool(data.get("overwrite")),
      )
      return 200, result if isinstance(result, dict) else {"ok": True, "result": result}

    if action == "eval_run":
      from gemcode.evals.harness import run_eval_suite

      include_llm = bool(data.get("include_llm"))
      result = run_eval_suite(
        project_root=root,
        include_llm=include_llm,
        model=str(data.get("model") or cfg.model),
      )
      return 200, {"ok": True, "result": result}

    if action == "autotune_init":
      from gemcode.autotune import init_autotune

      tag = str(data.get("tag") or "experiment").strip()
      result = init_autotune(project_root=root, tag=tag)
      return 200, {"ok": True, "result": result}

    if action == "autotune_eval":
      from gemcode.autotune import run_autotune_eval

      result = run_autotune_eval(
        project_root=root,
        include_llm=bool(data.get("include_llm")),
      )
      return 200, {"ok": True, "result": result}

    return 400, {"ok": False, "error": f"Unknown action: {action}"}
  except Exception as exc:
    return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _format_status_lines(cfg: GemCodeConfig, *, session_id: str | None) -> list[str]:
  lines = [
    f"model:          {cfg.model}",
    f"model_mode:     {cfg.model_mode}",
    f"session_id:     {session_id or '(web — pass session_id)'}",
    f"project_root:   {cfg.project_root}",
    "",
    "Capabilities:",
    f"  deep_research:  {'on' if cfg.enable_deep_research else 'off'}",
    f"  embeddings:     {'on' if cfg.enable_embeddings else 'off'}",
    f"  memory:         {'on' if cfg.enable_memory else 'off'}",
    f"  computer_use:   {'on' if cfg.enable_computer_use else 'off'}",
    f"  maps_grounding: {'on' if cfg.enable_maps_grounding else 'off'}",
    "",
    "Thinking:",
    f"  disabled:        {cfg.disable_thinking}",
    f"  level:           {cfg.thinking_level or '(auto)'}",
    f"  display:         {'verbose' if cfg.show_full_thinking else 'brief'}",
    "",
    "Permissions:",
    f"  permission_mode: {cfg.permission_mode}",
    f"  yes_to_all:      {cfg.yes_to_all}",
    f"  super_mode:      {getattr(cfg, 'super_mode', False)}",
    f"  max_llm_calls:   {cfg.max_llm_calls or '(default)'}",
  ]
  return lines


def _format_cost_lines(stats: Any) -> list[str]:
  from gemcode.pricing import format_cost, format_tokens

  lines = ["Session cost summary", "─" * 40]
  if not isinstance(stats, dict) or not stats:
    lines.append("No turn completed yet in this API process.")
    lines.append("Token/cost for your chat appears per-message in the UI.")
    return lines
  lines.append(f"  Last turn input tokens : {format_tokens(stats.get('in', 0) or 0)}")
  lines.append(f"  Last turn output tokens: {format_tokens(stats.get('out', 0) or 0)}")
  think = stats.get("think", 0) or 0
  if think:
    lines.append(f"  Last turn thinking     : {format_tokens(think)}")
  lc = stats.get("turn_cost")
  lines.append(f"  Last turn cost         : {format_cost(lc) if lc is not None else '(unknown)'}")
  lines.append(f"  Session total tokens   : {format_tokens(stats.get('session_total', 0) or 0)}")
  sc = stats.get("session_cost")
  if sc and sc > 0:
    lines.append(f"  Session total cost     : {format_cost(sc)}")
  return lines


def _format_context_static(cfg: GemCodeConfig) -> list[str]:
  from gemcode.context_warning import (
    get_auto_compact_threshold_tokens,
    get_effective_context_window_size_tokens,
  )

  eff = get_effective_context_window_size_tokens(cfg.model)
  aut = get_auto_compact_threshold_tokens(cfg.model)
  return [
    f"model: {cfg.model}",
    f"effective_context_window_tokens≈{eff}",
    f"autocompact_threshold_tokens≈{aut}",
  ]


def _list_openapi_specs(root: Path) -> list[dict[str, Any]]:
  openapi_dir = root / ".gemcode" / "openapi"
  if not openapi_dir.is_dir():
    return []
  specs: list[dict[str, Any]] = []
  for p in sorted(openapi_dir.iterdir()):
    if p.suffix.lower() not in (".yaml", ".yml", ".json"):
      continue
    auth = p.with_suffix(".auth")
    auth_json = openapi_dir / f"{p.stem}.auth.json"
    specs.append(
      {
        "name": p.stem,
        "path": str(p),
        "has_auth": auth.is_file() or auth_json.is_file(),
      }
    )
  return specs
