from pathlib import Path

from gemcode.agent import build_instruction
from gemcode.config import GemCodeConfig


def test_instruction_includes_runtime_facts(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert str(tmp_path.resolve()) in text
  assert "gemini-2.5-flash" in text
  assert "GEMCODE_MODEL" in text


def test_instruction_includes_veomem_tool_flow_when_recall_present(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  object.__setattr__(cfg, "_veomem_wakeup_text", "<veomem-context>hello</veomem-context>")
  text = build_instruction(cfg)
  assert "VeoMem recall" in text
  assert "veomem_search(query=...)" in text
  assert "veomem_timeline(id=...)" in text
  assert "veomem_get_observations(ids=...)" in text
