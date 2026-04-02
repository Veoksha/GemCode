from pathlib import Path

import pytest

from gemcode.paths import PathEscapeError, resolve_under_root


def test_resolve_under_root_ok(tmp_path: Path) -> None:
  (tmp_path / "a" / "b").mkdir(parents=True)
  p = resolve_under_root(tmp_path, "a/b")
  assert p.is_dir()


def test_resolve_rejects_escape(tmp_path: Path) -> None:
  with pytest.raises(PathEscapeError):
    resolve_under_root(tmp_path, "../../etc/passwd")
