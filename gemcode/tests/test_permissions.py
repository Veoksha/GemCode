from pathlib import Path
from unittest.mock import MagicMock

from gemcode.config import GemCodeConfig
from gemcode.permissions import make_before_tool_callback


def test_write_blocked_without_yes(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.yes_to_all = False
  cb = make_before_tool_callback(cfg)
  tool = MagicMock()
  tool.name = "write_file"
  out = cb(tool, {"path": "x", "content": "y"}, None)
  assert out is not None
  assert "error" in out


def test_write_allowed_with_yes(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.yes_to_all = True
  cb = make_before_tool_callback(cfg)
  tool = MagicMock()
  tool.name = "write_file"
  assert cb(tool, {"path": "x", "content": "y"}, None) is None
