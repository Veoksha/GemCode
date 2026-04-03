from pathlib import Path

from gemcode.agent import build_instruction
from gemcode.config import GemCodeConfig


def test_instruction_includes_runtime_facts(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert str(tmp_path.resolve()) in text
  assert "gemini-2.5-flash" in text
  assert "list-models" in text
  assert "GEMCODE_MODEL" in text
