from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.permissions import make_before_tool_callback


class ComputerUseTool:
  # Match callbacks._is_computer_use_tool() heuristics.
  __module__ = "google.adk.tools.computer_use.computer_use_tool"

  def __init__(self, name: str = "click_at"):
    self.name = name


def test_computer_use_blocked_without_yes(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.permission_mode = "default"
  cfg.yes_to_all = False
  cb = make_before_tool_callback(cfg)

  tool = ComputerUseTool("click_at")
  out = cb(tool, {"x": 1, "y": 2}, None)
  assert out is not None
  assert out.get("error_kind") == "permission_denied"


def test_computer_use_allowed_with_yes(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.permission_mode = "default"
  cfg.yes_to_all = True
  cb = make_before_tool_callback(cfg)

  tool = ComputerUseTool("navigate")
  assert cb(tool, {"url": "https://example.com"}, None) is None


def test_computer_use_blocked_in_strict_mode(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.permission_mode = "strict"
  cfg.yes_to_all = True  # still blocked in strict
  cb = make_before_tool_callback(cfg)

  tool = ComputerUseTool("type_text_at")
  out = cb(tool, {"x": 1, "y": 2, "text": "hi"}, None)
  assert out is not None
  assert out.get("error_kind") == "permission_denied"
  assert "computer use" in str(out.get("error", "")).lower()

