from pathlib import Path
from unittest.mock import MagicMock

import asyncio

from gemcode.config import GemCodeConfig
from gemcode.repl_slash import process_repl_slash
from gemcode.tools.filesystem import make_filesystem_tools
from gemcode.trust import trust_root


def test_add_dir_and_read_file(tmp_path: Path) -> None:
  other = tmp_path / "otherrepo"
  other.mkdir()
  (other / "README.md").write_text("hello", encoding="utf-8")

  cfg = GemCodeConfig(project_root=tmp_path)
  trust_root(tmp_path, trusted=True)
  runner = MagicMock()

  out_lines: list[str] = []

  def _print(*args, **kwargs):
    out_lines.append(" ".join(str(a) for a in args))

  asyncio.run(
    process_repl_slash(
      cfg=cfg,
      runner=runner,
      session_id="sess",
      prompt_text=f"/add-dir {other}",
      print_fn=_print,
      extra_tools=None,
    )
  )

  assert getattr(cfg, "_added_dirs", None)
  name = next(iter(cfg._added_dirs.keys()))

  read_file, _, _, _, _ = make_filesystem_tools(cfg)
  res = read_file(f"{name}/README.md")
  assert res.get("content") == "hello"

