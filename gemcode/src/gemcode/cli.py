"""CLI entry: `gemcode "prompt"`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from gemcode.config import GemCodeConfig, load_dotenv_optional
from gemcode.invoke import run_turn
from gemcode.model_routing import pick_effective_model
from gemcode.capability_routing import apply_capability_routing
from gemcode.session_runtime import create_runner


def _events_to_text(events) -> str:
  parts: list[str] = []
  for event in events:
    if not event.content or not event.content.parts:
      continue
    for part in event.content.parts:
      if part.text and event.author and event.author != "user":
        parts.append(part.text)
  return "".join(parts)


async def _run_prompt(
  cfg: GemCodeConfig, prompt: str, session_id: str, *, use_mcp: bool
) -> str:
  load_dotenv_optional()
  extra: list = []
  if use_mcp:
    from gemcode.mcp_loader import load_mcp_toolsets

    extra = load_mcp_toolsets(cfg)

  runner = create_runner(cfg, extra_tools=extra or None)
  try:
    collected = await run_turn(
        runner,
        user_id="local",
        session_id=session_id,
        prompt=prompt,
        max_llm_calls=cfg.max_llm_calls,
        cfg=cfg,
    )
    return _events_to_text(collected)
  finally:
    # Ensure toolsets with external resources (e.g. Playwright browser) are
    # cleaned up after each CLI invocation.
    await runner.close()


def main() -> None:
  # Quick command bypass (no prompt parsing): list available Gemini models.
  if (
    len(sys.argv) > 1
    and sys.argv[1] in ("models", "list-models", "list_models")
  ):
    load_dotenv_optional()
    from google.genai import Client

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
      raise SystemExit("GOOGLE_API_KEY is not set. Copy .env.example -> .env and retry.")

    client = Client(api_key=api_key)
    models = client.models.list()
    show_all = "--show-all" in sys.argv[2:]
    # `models.list()` returns objects; print best-effort fields.
    for m in models:
      name = getattr(m, "name", None)
      actions = getattr(m, "supported_actions", None)
      if not name:
        continue
      if not show_all and actions and isinstance(actions, list):
        # GemCode uses an ADK LlmAgent; it relies on `generateContent`-style models.
        if "generateContent" not in actions:
          continue
      if actions and isinstance(actions, list):
        print(f"{name}\t{','.join(actions)}")
      else:
        print(name)
    return

  # Live audio mode (Gemini Live API via ADK run_live()).
  if len(sys.argv) > 1 and sys.argv[1] == "live-audio":
    audio_parser = argparse.ArgumentParser(
      prog="gemcode live-audio",
      description="GemCode live audio (mic -> Gemini Live)",
    )
    audio_parser.add_argument(
      "-C",
      "--directory",
      type=Path,
      default=Path.cwd(),
      help="Project root",
    )
    audio_parser.add_argument(
      "--session",
      default=None,
      help="Session id for SQLite-backed history (optional)",
    )
    audio_parser.add_argument(
      "--seconds",
      type=int,
      default=10,
      help="Record mic for N seconds before sending audio",
    )
    audio_parser.add_argument(
      "--rate",
      type=int,
      default=24000,
      help="Input PCM sample rate (Hz)",
    )
    audio_parser.add_argument(
      "--language",
      default=None,
      help="Optional BCP-47 language code (e.g. en-US)",
    )
    audio_parser.add_argument(
      "--yes",
      action="store_true",
      help="Allow write_file / search_replace",
    )
    audio_parser.add_argument(
      "--model",
      default=None,
      help="Override GEMCODE_MODEL (must support AUDIO live streaming)",
    )
    audio_parser.add_argument(
      "--deep-research",
      action="store_true",
      help="Enable deep research tools + routing",
    )
    audio_parser.add_argument(
      "--embeddings",
      action="store_true",
      help="Enable embeddings-based semantic retrieval",
    )

    args = audio_parser.parse_args(sys.argv[2:])
    load_dotenv_optional()

    cfg = GemCodeConfig(project_root=args.directory)
    cfg.yes_to_all = args.yes
    cfg.enable_deep_research = bool(args.deep_research)
    cfg.enable_embeddings = bool(args.embeddings)
    if args.model:
      cfg.model = args.model
    else:
      cfg.model = cfg.model_audio_live

    session_id = args.session or str(uuid.uuid4())
    from gemcode.live_audio_engine import run_live_audio

    asyncio.run(
      run_live_audio(
        cfg,
        session_id=session_id,
        seconds=args.seconds,
        input_rate=args.rate,
        language_code=args.language,
      )
    )
    print(f"\n[gemcode live-audio] session_id={session_id}", file=sys.stderr)
    return

  parser = argparse.ArgumentParser(prog="gemcode", description="Gemini + ADK coding agent")
  parser.add_argument(
    "prompt",
    nargs="?",
    default=None,
    help="Task or question (read from stdin if omitted)",
  )
  parser.add_argument("-C", "--directory", type=Path, default=Path.cwd(), help="Project root")
  parser.add_argument("--session", default=None, help="Session id for SQLite-backed history")
  parser.add_argument("--yes", action="store_true", help="Allow write_file / search_replace")
  parser.add_argument("--model", default=None, help="Override GEMCODE_MODEL")
  parser.add_argument(
      "--model-mode",
      default=None,
      help="Model mode: auto|fast|balanced|quality (overrides GEMCODE_MODEL_MODE)",
  )
  parser.add_argument(
    "--deep-research",
    action="store_true",
    help="Enable deep research tools and route to the deep-research model",
  )
  parser.add_argument(
    "--maps-grounding",
    action="store_true",
    help="Opt-in to Google Maps grounding tool inside deep-research (may be incompatible with other built-in tools depending on model/tooling).",
  )
  parser.add_argument(
    "--embeddings",
    action="store_true",
    help="Enable embeddings-based semantic retrieval (and embedding memory if enabled)",
  )
  parser.add_argument(
    "--capability-mode",
    default=None,
    help="Capability routing: auto|research|embeddings|computer|audio|all (enables tools and routes models)",
  )
  parser.add_argument(
    "--tool-combination-mode",
    default=None,
    help="Gemini 3 tool context circulation: deep_research|always|never|auto",
  )
  parser.add_argument("--mcp", action="store_true", help="Load .gemcode/mcp.json toolsets")
  parser.add_argument(
      "--max-llm-calls",
      type=int,
      default=None,
      metavar="N",
      help="Cap model↔tool iterations for this message (maps to ADK RunConfig.max_llm_calls)",
  )
  args = parser.parse_args()

  load_dotenv_optional()
  prompt = args.prompt
  if prompt is None:
    prompt = sys.stdin.read()
  if not prompt.strip():
    parser.error("Empty prompt")

  cfg = GemCodeConfig(project_root=args.directory)
  if args.model:
    cfg.model_overridden = True
  if args.model:
    cfg.model = args.model
    # User explicitly picked a model id, so treat it as primary.
    cfg.model_family_mode = "primary"
    # If the user explicitly sets a model, default to fast mode unless
    # `--model-mode` is also provided.
    if args.model_mode is None:
      cfg.model_mode = "fast"
  cfg.yes_to_all = args.yes
  cfg.enable_deep_research = bool(args.deep_research)
  cfg.enable_maps_grounding = bool(args.maps_grounding)
  cfg.enable_embeddings = bool(args.embeddings)
  if args.capability_mode is not None:
    cfg.capability_mode = args.capability_mode
  if args.tool_combination_mode is not None:
    cfg.tool_combination_mode = args.tool_combination_mode
  if args.model_mode is not None:
    cfg.model_mode = args.model_mode
  if args.max_llm_calls is not None:
    cfg.max_llm_calls = args.max_llm_calls

  session_id = args.session or str(uuid.uuid4())
  prompt_text = prompt.strip()
  apply_capability_routing(cfg, prompt_text, context="prompt")
  cfg.model = pick_effective_model(cfg, prompt_text)
  out = asyncio.run(_run_prompt(cfg, prompt_text, session_id, use_mcp=args.mcp))
  if out:
    print(out)
  print(f"\n[gemcode] session_id={session_id}", file=sys.stderr)


if __name__ == "__main__":
  main()
