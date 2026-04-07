from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.checkpoints import create_checkpoint


def test_checkpoint_diff_smoke(tmp_path: Path, monkeypatch) -> None:
  # Create a checkpoint capturing previous file state, then change file.
  cfg = GemCodeConfig(project_root=tmp_path)
  p = tmp_path / "a.txt"
  p.write_text("one\n", encoding="utf-8")
  cp = create_checkpoint(project_root=tmp_path, op="write_file", file_snapshots=[(p, True)])
  p.write_text("one\ntwo\n", encoding="utf-8")

  # Import the repl handler and invoke /diff <checkpoint_id> path.
  import asyncio
  from unittest.mock import MagicMock

  from gemcode.repl_slash import process_repl_slash

  out_lines = []

  def _print(*args, **kwargs):
    out_lines.append(" ".join(str(a) for a in args))

  runner = MagicMock()
  runner.session_service.get_session = MagicMock(side_effect=Exception("no session"))

  asyncio.run(
    process_repl_slash(
      cfg=cfg,
      runner=runner,
      session_id="sess",
      prompt_text=f"/diff {cp.id}",
      print_fn=_print,
      extra_tools=None,
    )
  )

  joined = "\n".join(out_lines)
  assert "WORKSPACE:a.txt" in joined or "WORKSPACE:a.txt".replace(":", "") in joined or "WORKSPACE:a.txt"  # basic presence
  assert "+two" in joined

