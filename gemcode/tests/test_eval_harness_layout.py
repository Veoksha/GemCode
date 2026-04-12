"""Layout detection for eval harness (pytest cwd)."""

from __future__ import annotations

from pathlib import Path

from gemcode.evals.harness import _discover_pytest_cwd


def test_discover_pytest_prefers_root_tests(tmp_path: Path) -> None:
  (tmp_path / "tests").mkdir()
  got = _discover_pytest_cwd(tmp_path)
  assert got is not None
  cwd, env = got
  assert cwd == tmp_path
  assert env is None


def test_discover_pytest_gemcode_subpackage(tmp_path: Path) -> None:
  gc = tmp_path / "gemcode"
  (gc / "tests").mkdir(parents=True)
  got = _discover_pytest_cwd(tmp_path)
  assert got is not None
  cwd, env = got
  assert cwd == gc
  assert env is not None
  assert "PYTHONPATH" in env
  assert env["PYTHONPATH"].startswith("src")


def test_discover_pytest_none_when_no_tests(tmp_path: Path) -> None:
  assert _discover_pytest_cwd(tmp_path) is None
