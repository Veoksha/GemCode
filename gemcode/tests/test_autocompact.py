from __future__ import annotations

from pathlib import Path

from google.genai import types

import gemcode.autocompact as ac
from gemcode.config import GemCodeConfig


class _Ctx:
  def __init__(self):
    self.state = {}


class _Req:
  def __init__(self, contents):
    self.contents = contents


def test_autocompact_rewrites_contents(monkeypatch) -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  cfg.max_context_chars = 10_000

  # Force low threshold so test triggers.
  monkeypatch.setattr(ac, "_autocompact_threshold_chars", lambda _cfg: 100)
  monkeypatch.setattr(ac, "_tail_keep_contents", lambda _cfg: 3)
  monkeypatch.setattr(ac, "_summarize_via_genai", lambda _cfg, _p: "SUMMARY")

  contents = [
      types.Content(role="user", parts=[types.Part(text="INSTR")]),
      types.Content(role="user", parts=[types.Part(text="old1 " * 50)]),
      types.Content(role="model", parts=[types.Part(text="old2 " * 50)]),
      types.Content(role="user", parts=[types.Part(text="keep1")]),
      types.Content(role="model", parts=[types.Part(text="keep2")]),
      types.Content(role="user", parts=[types.Part(text="keep3")]),
  ]
  req = _Req(contents)
  cb = ac.make_before_model_autocompact_callback(cfg)
  assert cb is not None
  ctx = _Ctx()

  # Run callback
  import asyncio

  asyncio.run(cb(ctx, req))

  # Keeps first + inserts summary + keeps tail
  assert req.contents[0].parts[0].text == "INSTR"
  assert "Conversation summary" in req.contents[1].parts[0].text
  assert "SUMMARY" in req.contents[1].parts[0].text
  assert req.contents[-1].parts[0].text == "keep3"


def test_autocompact_circuit_breaker(monkeypatch) -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  monkeypatch.setattr(ac, "_autocompact_threshold_chars", lambda _cfg: 1)
  monkeypatch.setattr(ac, "_max_failures", lambda: 1)
  monkeypatch.setattr(ac, "_summarize_via_genai", lambda _cfg, _p: (_ for _ in ()).throw(RuntimeError("fail")))

  req = _Req([types.Content(role="user", parts=[types.Part(text="x" * 200)])])
  cb = ac.make_before_model_autocompact_callback(cfg)
  ctx = _Ctx()
  ctx.state["gemcode:autocompact_failures"] = 1

  import asyncio

  asyncio.run(cb(ctx, req))
  # No change when breaker tripped
  assert len(req.contents) == 1

