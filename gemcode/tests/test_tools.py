from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.tools.edit import make_edit_tools
from gemcode.tools.filesystem import make_filesystem_tools


def test_read_file(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
  read_file, _, _ = make_filesystem_tools(cfg)
  out = read_file("x.txt")
  assert out["content"] == "hello"


def test_search_replace(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
  _, search_replace = make_edit_tools(cfg)
  out = search_replace("f.py", "a = 1", "a = 2")
  assert "error" not in out
  assert (tmp_path / "f.py").read_text() == "a = 2\n"
